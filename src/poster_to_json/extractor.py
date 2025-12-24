#!/usr/bin/env python3
"""
Poster content extractor - Extracts structured JSON from scientific posters.

Uses:
- pdfalto for PDF text extraction
- PyMuPDF as fallback
- Qwen2.5-VL (Transformers) for image-based poster OCR
- Llama 3.1 8B (Ollama) for JSON structuring

The extracted JSON conforms to the posters-science schema.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import logging
import unicodedata
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Tuple

import numpy as np
import fitz  # PyMuPDF
from PIL import Image
import ollama

logger = logging.getLogger(__name__)


# Ollama model for JSON structuring
OLLAMA_JSON_MODEL = "llama3.1:8b-instruct-q8_0"

# Transformers model for vision OCR
QWEN_VL_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"


def log(msg: str):
    """Timestamped logging."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


class VisionOCR:
    """
    Vision OCR using Qwen2.5-VL via Transformers.
    
    This provides higher quality OCR than Ollama's Qwen3-VL 4B.
    """
    
    def __init__(
        self,
        model_name: str = QWEN_VL_MODEL,
        device: str = "cuda",
        load_in_4bit: bool = True,
    ):
        """
        Initialize vision OCR model.
        
        Args:
            model_name: HuggingFace model name for Qwen2.5-VL
            device: Device to run on ("cuda" or "cpu")
            load_in_4bit: Whether to use 4-bit quantization
        """
        self.model_name = model_name
        self.device = device
        self.load_in_4bit = load_in_4bit
        self.model = None
        self.processor = None
        self._loaded = False
    
    def load(self):
        """Load the model (lazy loading)."""
        if self._loaded:
            return
        
        try:
            from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
            from qwen_vl_utils import process_vision_info
            
            log(f"Loading Qwen2.5-VL model: {self.model_name}")
            
            load_kwargs = {
                "torch_dtype": "auto",
                "device_map": "auto" if self.device == "cuda" else None,
            }
            
            if self.load_in_4bit:
                from transformers import BitsAndBytesConfig
                load_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype="float16",
                )
            
            self.model = Qwen2VLForConditionalGeneration.from_pretrained(
                self.model_name,
                **load_kwargs
            )
            self.processor = AutoProcessor.from_pretrained(self.model_name)
            self._loaded = True
            log(f"   ✓ Qwen2.5-VL loaded")
            
        except ImportError as e:
            raise ImportError(
                "Transformers dependencies not installed. "
                "Install with: pip install poster-to-json[transformers]"
            ) from e
    
    def extract_text(self, image_path: str) -> str:
        """
        Extract text from an image using Qwen2.5-VL.
        
        Args:
            image_path: Path to image file
            
        Returns:
            Extracted text
        """
        self.load()
        
        from qwen_vl_utils import process_vision_info
        
        prompt = """Transcribe ALL visible text from this scientific poster exactly as written.

Include:
- Title and subtitle
- Author names and affiliations
- All section headers and content
- Algorithm/method descriptions
- Figure and table captions
- Numbers, statistics, equations
- References and URLs

Rules:
- Output the raw text ONLY
- Do NOT add explanations or interpretations
- Do NOT translate any text
- Preserve the original language
- Include all bullet points and lists
- Do NOT repeat any content"""

        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": image_path},
                {"type": "text", "text": prompt},
            ],
        }]
        
        # Process inputs
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        
        inputs = self.processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self.model.device)
        
        # Generate
        generated_ids = self.model.generate(
            **inputs,
            max_new_tokens=4000,
            temperature=0.1,
            do_sample=True,
        )
        
        # Decode
        generated_ids_trimmed = [
            out_ids[len(in_ids):]
            for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        output_text = self.processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        
        return self._deduplicate_lines(output_text)
    
    def _deduplicate_lines(self, text: str) -> str:
        """Remove duplicate lines."""
        lines = text.split('\n')
        seen = set()
        result = []
        for line in lines:
            norm = re.sub(r'\s+', ' ', line).strip().lower()
            if norm and norm not in seen:
                result.append(line)
                seen.add(norm)
            elif not norm:
                result.append(line)
        return '\n'.join(result)
    
    def unload(self):
        """Unload model to free GPU memory."""
        if self._loaded:
            del self.model
            del self.processor
            self.model = None
            self.processor = None
            self._loaded = False
            
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            log("   ✓ Qwen2.5-VL unloaded")


class PosterExtractor:
    """
    Extracts structured JSON from scientific posters.
    
    Uses pdfalto for PDF text extraction and Qwen2.5-VL for image OCR,
    with Llama 3.1 8B for JSON structuring.
    """
    
    # Primary extraction prompt
    EXTRACTION_PROMPT = """Convert this scientific poster text to JSON format.

CRITICAL RULES FOR SECTIONS:
1. Create a SEPARATE section for EACH distinct topic/header found in the poster
2. Common section headers include:
   - Abstract, Introduction, Background
   - Methods, Methodology, Materials and Methods
   - Results, Key Findings, Findings (SEPARATE sections if both exist!)
   - Discussion, Conclusions, Discussion/Conclusions
   - References (MUST contain numbered citations like "1. Author..." NOT findings!)
   - Acknowledgements, Contact, Funding
3. Each section must have its OWN "sectionTitle" and "sectionContent"
4. Do NOT merge multiple topics into one section
5. Copy ALL text EXACTLY - do not paraphrase or summarize
6. IMPORTANT: "Key Findings" and "References" are DIFFERENT sections:
   - Key Findings = bullet points about discoveries/results
   - References = numbered bibliography citations with author names and years

JSON SCHEMA:
{{
  "creators": [
    {{"name": "LastName, FirstName", "affiliation": [{{"name": "Institution Name"}}]}}
  ],
  "titles": [{{"title": "Main Poster Title"}}],
  "posterContent": {{
    "sections": [
      {{"sectionTitle": "First Section Header", "sectionContent": "Complete verbatim text of first section"}},
      {{"sectionTitle": "Second Section Header", "sectionContent": "Complete verbatim text of second section"}},
      {{"sectionTitle": "Third Section Header", "sectionContent": "Complete verbatim text..."}},
      ...continue for ALL sections found in the poster...
    ]
  }},
  "imageCaption": [{{"caption1": "Figure 1 caption text"}}],
  "tableCaption": [{{"caption1": "Table 1 caption text"}}]
}}

EXAMPLE - A poster with 8 sections should produce 8 section objects.

POSTER TEXT TO CONVERT:
{raw_text}

OUTPUT VALID JSON ONLY:"""

    def __init__(
        self,
        pdfalto_path: Optional[str] = None,
        ollama_model: str = OLLAMA_JSON_MODEL,
        use_transformers_ocr: bool = True,
    ):
        """
        Initialize extractor.
        
        Args:
            pdfalto_path: Path to pdfalto binary
            ollama_model: Ollama model for JSON structuring
            use_transformers_ocr: Use Qwen2.5-VL Transformers for OCR (vs Ollama)
        """
        self.pdfalto_path = pdfalto_path or self._find_pdfalto()
        self.ollama_model = ollama_model
        self.use_transformers_ocr = use_transformers_ocr
        
        self._vision_ocr = None
        self._ollama_ready = False
    
    def _find_pdfalto(self) -> Optional[str]:
        """Find pdfalto binary."""
        env_path = os.environ.get("PDFALTO_PATH")
        if env_path and os.path.exists(env_path):
            return env_path
        
        path_binary = shutil.which("pdfalto")
        if path_binary:
            return path_binary
        
        return None
    
    def _ensure_ollama_ready(self):
        """Ensure Ollama model is available."""
        if self._ollama_ready:
            return
        
        log(f"Checking Ollama model: {self.ollama_model}")
        try:
            ollama.show(self.ollama_model)
            log(f"   ✓ Ollama model ready: {self.ollama_model}")
        except ollama.ResponseError:
            log(f"   Pulling {self.ollama_model}...")
            ollama.pull(self.ollama_model)
            log(f"   ✓ Model pulled: {self.ollama_model}")
        
        self._ollama_ready = True
    
    def _get_vision_ocr(self) -> VisionOCR:
        """Get or create vision OCR instance."""
        if self._vision_ocr is None:
            self._vision_ocr = VisionOCR()
        return self._vision_ocr
    
    def extract_text_pdfalto(self, pdf_path: str) -> Optional[str]:
        """Extract text from PDF using pdfalto."""
        if self.pdfalto_path is None:
            return None
        
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                xml_path = os.path.join(tmpdir, "output.xml")
                result = subprocess.run(
                    [self.pdfalto_path, "-noImage", "-readingOrder", pdf_path, xml_path],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0 or not os.path.exists(xml_path):
                    return None
                return self._parse_alto_xml(xml_path)
        except Exception as e:
            logger.error(f"pdfalto error: {e}")
            return None
    
    def _parse_alto_xml(self, xml_path: str) -> str:
        """Parse ALTO XML output."""
        from xml.etree import ElementTree as ET
        
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            
            ns = {"alto": "http://www.loc.gov/standards/alto/ns-v3#"}
            text_blocks = root.findall(".//alto:TextBlock", ns)
            if not text_blocks:
                text_blocks = root.findall(".//TextBlock")
            
            lines = []
            for block in text_blocks:
                strings = block.findall(".//alto:String", ns)
                if not strings:
                    strings = block.findall(".//String")
                words = [s.get("CONTENT", "") for s in strings if s.get("CONTENT")]
                if words:
                    lines.append(" ".join(words))
            
            return "\n".join(lines)
        except Exception:
            return ""
    
    def extract_text_pymupdf(self, pdf_path: str) -> str:
        """Fallback PDF extraction using PyMuPDF."""
        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
        return text.strip()
    
    def extract_text_vision(self, image_path: str) -> str:
        """Extract text from image using vision OCR."""
        if self.use_transformers_ocr:
            return self._get_vision_ocr().extract_text(image_path)
        else:
            # Fallback to Ollama (lower quality but no extra deps)
            return self._extract_text_ollama_vision(image_path)
    
    def _extract_text_ollama_vision(self, image_path: str) -> str:
        """Extract text using Ollama vision model (fallback)."""
        prompt = """Transcribe ALL visible text from this scientific poster exactly as written.
Include all text, headers, captions, and references.
Output raw text ONLY, no explanations."""

        try:
            response = ollama.chat(
                model="qwen2.5-vl:7b",  # Or llava if qwen not available
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [image_path],
                }],
                options={"num_predict": 4000, "temperature": 0},
            )
            return response.message.content
        except Exception as e:
            logger.error(f"Ollama vision OCR failed: {e}")
            return ""
    
    def get_raw_text(self, poster_path: str) -> Tuple[str, str]:
        """
        Get raw text from poster file.
        
        Args:
            poster_path: Path to PDF or image file
            
        Returns:
            Tuple of (text, source) where source is 'pdfalto', 'pymupdf', or 'vision'
        """
        ext = Path(poster_path).suffix.lower()
        
        if ext in [".jpg", ".jpeg", ".png", ".tiff", ".tif"]:
            return self.extract_text_vision(poster_path), "vision"
        
        if ext == ".pdf":
            # Try pdfalto first
            text = self.extract_text_pdfalto(poster_path)
            if text and len(text) > 500:
                return text, "pdfalto"
            
            # Fallback to PyMuPDF
            text = self.extract_text_pymupdf(poster_path)
            if text and len(text) > 500:
                return text, "pymupdf"
            
            # If still no text, try vision OCR on first page
            log("   PDF has little text, trying vision OCR...")
            with tempfile.TemporaryDirectory() as tmpdir:
                doc = fitz.open(poster_path)
                if len(doc) > 0:
                    pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_path = os.path.join(tmpdir, "page.png")
                    pix.save(img_path)
                    doc.close()
                    return self.extract_text_vision(img_path), "vision"
                doc.close()
            
            return text, "pymupdf"
        
        return "", "unknown"
    
    def structure_to_json(self, raw_text: str) -> Dict:
        """
        Structure raw text into JSON using Llama 3.1.
        
        Args:
            raw_text: Extracted raw text from poster
            
        Returns:
            Structured JSON dictionary
        """
        self._ensure_ollama_ready()
        
        prompt = self.EXTRACTION_PROMPT.format(raw_text=raw_text)
        
        response = ollama.chat(
            model=self.ollama_model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "num_ctx": 32768,
                "num_predict": 18000,
                "temperature": 0,
            },
        )
        
        response_text = response.message.content
        return self._parse_json_response(response_text)
    
    def _parse_json_response(self, response: str) -> Dict:
        """Parse JSON from LLM response with error handling."""
        response = response.strip()
        
        # Remove markdown code blocks
        if response.startswith("```json"):
            response = response[7:]
        elif response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        # Find JSON start
        start = response.find("{")
        if start == -1:
            return {"error": "No JSON found", "raw": response[:3000]}
        
        json_str = response[start:]
        
        # Try to parse
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        # Try to repair
        json_str = self._repair_json(json_str)
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {"error": "JSON parse failed", "raw": json_str[:3000]}
    
    def _repair_json(self, s: str) -> str:
        """Attempt to repair malformed JSON."""
        # Remove trailing commas
        s = re.sub(r",\s*([}\]])", r"\1", s)
        
        # Fix truncation
        in_string = False
        escape = False
        open_braces = 0
        open_brackets = 0
        
        for c in s:
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                open_braces += 1
            elif c == "}":
                open_braces -= 1
            elif c == "[":
                open_brackets += 1
            elif c == "]":
                open_brackets -= 1
        
        # Close unclosed brackets/braces
        s = s.rstrip()
        if s.endswith(","):
            s = s[:-1]
        s += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        
        return s
    
    def extract(self, poster_path: str) -> Dict:
        """
        Extract structured JSON from a poster file.
        
        Args:
            poster_path: Path to PDF or image file
            
        Returns:
            Extracted JSON dictionary with poster content
        """
        log(f"Extracting: {poster_path}")
        
        # Get raw text
        raw_text, source = self.get_raw_text(poster_path)
        
        if not raw_text or source == "unknown":
            return {"error": "Failed to extract text"}
        
        log(f"   Extracted {len(raw_text)} chars using {source}")
        
        # Structure to JSON
        result = self.structure_to_json(raw_text)
        
        # Add metadata
        result["_extraction_source"] = source
        result["_extracted_at"] = datetime.now().isoformat()
        
        return result
    
    def cleanup(self):
        """Cleanup resources (unload models)."""
        if self._vision_ocr is not None:
            self._vision_ocr.unload()
            self._vision_ocr = None

