#!/usr/bin/env python3
"""
Poster content extractor - Extracts structured JSON from scientific posters.

Uses:
- pdfalto for PDF text extraction
- PyMuPDF as fallback
- Qwen2-VL-7B (Transformers) for image-based poster OCR
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

# Transformers model for vision OCR (Qwen2-VL, not 2.5 which has compatibility issues)
QWEN_VL_MODEL = "Qwen/Qwen2-VL-7B-Instruct"


def log(msg: str):
    """Timestamped logging."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


class VisionOCR:
    """
    Vision OCR using Qwen2-VL via Transformers.
    
    This provides higher quality OCR than Ollama's vision models.
    """
    
    def __init__(
        self,
        model_name: str = QWEN_VL_MODEL,
        device: str = "cuda:0",  # Use GPU 0 (RTX 3080) - GPU 1 (RTX 4090) is for Ollama
        load_in_4bit: bool = True,
    ):
        """
        Initialize vision OCR model.
        
        Args:
            model_name: HuggingFace model name for Qwen2-VL
            device: Device to run on ("cuda:0", "cuda:1", or "cpu")
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
            import torch
            from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
            from qwen_vl_utils import process_vision_info
            
            # Determine which GPU to use
            # Check available GPU memory and pick the one with most free memory
            vision_device = self._select_best_gpu()
            
            log(f"Loading Qwen2-VL model: {self.model_name} on {vision_device}")
            
            load_kwargs = {
                "torch_dtype": torch.bfloat16,
            }
            
            if self.load_in_4bit:
                from transformers import BitsAndBytesConfig
                load_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                )
                load_kwargs["device_map"] = vision_device
            else:
                load_kwargs["device_map"] = vision_device
            
            self.model = Qwen2VLForConditionalGeneration.from_pretrained(
                self.model_name,
                **load_kwargs
            )
            self.processor = AutoProcessor.from_pretrained(self.model_name)
            self._loaded = True
            self._device = vision_device
            log(f"   ✓ Qwen2-VL loaded on {vision_device}")
            
        except ImportError as e:
            raise ImportError(
                "Transformers dependencies not installed. "
                "Install with: pip install poster-to-json[transformers]"
            ) from e
    
    def _select_best_gpu(self) -> str:
        """Select GPU with most free memory (avoids Ollama's GPU)."""
        import torch
        
        if not torch.cuda.is_available():
            return "cpu"
        
        num_gpus = torch.cuda.device_count()
        if num_gpus == 1:
            return "cuda:0"
        
        # Find GPU with most free memory
        best_gpu = 0
        best_free = 0
        
        for i in range(num_gpus):
            try:
                free_mem = torch.cuda.mem_get_info(i)[0]  # Returns (free, total)
                total_mem = torch.cuda.mem_get_info(i)[1]
                log(f"   GPU {i}: {free_mem // (1024**3)}GB free of {total_mem // (1024**3)}GB")
                if free_mem > best_free:
                    best_free = free_mem
                    best_gpu = i
            except Exception:
                continue
        
        return f"cuda:{best_gpu}"
    
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
            
            log("   ✓ Qwen2-VL unloaded")


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
        use_transformers_ocr: bool = False,  # Default to Ollama vision (efficient GPU sharing)
    ):
        """
        Initialize extractor.
        
        Args:
            pdfalto_path: Path to pdfalto binary
            ollama_model: Ollama model for JSON structuring
            use_transformers_ocr: Use Qwen2-VL Transformers for OCR (vs Ollama qwen2.5vl)
        """
        self.pdfalto_path = pdfalto_path or self._find_pdfalto()
        self.ollama_model = ollama_model
        self.use_transformers_ocr = use_transformers_ocr
        
        self._vision_ocr = None
        self._ollama_ready = False
    
    def _find_pdfalto(self) -> Optional[str]:
        """Find pdfalto binary."""
        # Check environment variable first
        env_path = os.environ.get("PDFALTO_PATH")
        if env_path and os.path.exists(env_path):
            log(f"   Found pdfalto via PDFALTO_PATH: {env_path}")
            return env_path
        
        # Check PATH
        path_binary = shutil.which("pdfalto")
        if path_binary:
            log(f"   Found pdfalto in PATH: {path_binary}")
            return path_binary
        
        # Check common locations
        common_paths = [
            "/usr/local/bin/pdfalto",
            "/usr/bin/pdfalto",
            os.path.expanduser("~/pdfalto/pdfalto"),
            # Docker location
            "/app/pdfalto/pdfalto",
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                log(f"   Found pdfalto at: {path}")
                return path
        
        log("   WARNING: pdfalto not found, will use PyMuPDF fallback")
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
            try:
                return self._get_vision_ocr().extract_text(image_path)
            except Exception as e:
                log(f"   Transformers OCR failed ({e}), falling back to Ollama...")
                return self._extract_text_ollama_vision(image_path)
        else:
            # Use Ollama vision (efficient GPU sharing with Llama 3.1)
            return self._extract_text_ollama_vision(image_path)
    
    def _extract_text_ollama_vision(self, image_path: str) -> str:
        """Extract text using Ollama vision model (efficient GPU sharing with Llama 3.1)."""
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
- Include all bullet points and lists"""

        # Try qwen2.5vl first, then fallback to other vision models
        vision_models = ["qwen2.5vl:7b", "qwen3-vl:4b-instruct-q8_0", "llava"]
        
        for model in vision_models:
            try:
                log(f"   Using Ollama vision model: {model}")
                response = ollama.chat(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": prompt,
                        "images": [image_path],
                    }],
                    options={"num_predict": 4000, "temperature": 0},
                )
                text = response.message.content
                if text and len(text) > 100:
                    return text
            except Exception as e:
                log(f"   Ollama vision model {model} failed: {e}")
                continue
        
        logger.error("All Ollama vision models failed")
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
    
    # Shorter fallback prompt for when primary prompt causes truncation
    FALLBACK_PROMPT = """Extract JSON from this poster text. Include creators, titles, posterContent with sections.
Output ONLY valid JSON:

{raw_text}

JSON:"""

    def structure_to_json(self, raw_text: str) -> Dict:
        """
        Structure raw text into JSON using Llama 3.1 with retry logic.
        
        Args:
            raw_text: Extracted raw text from poster
            
        Returns:
            Structured JSON dictionary
        """
        self._ensure_ollama_ready()
        
        # Preprocess to remove problematic characters
        raw_text = self._preprocess_text(raw_text)
        
        prompt = self.EXTRACTION_PROMPT.format(raw_text=raw_text)
        
        # First attempt with standard tokens
        response = self._call_ollama(prompt, num_predict=18000)
        result = self._parse_json_response(response)
        
        # If truncation/error detected, retry with more tokens
        if "error" in result or result.get("_truncated"):
            log("   Truncation detected, retrying with more tokens...")
            response = self._call_ollama(prompt, num_predict=24000)
            result = self._parse_json_response(response)
        
        # If still failing, try shorter fallback prompt
        if "error" in result or (result.get("_truncated") and not result.get("posterContent", {}).get("sections")):
            log("   Still truncated, trying shorter prompt...")
            fallback_prompt = self.FALLBACK_PROMPT.format(raw_text=raw_text)
            response = self._call_ollama(fallback_prompt, num_predict=24000)
            result = self._parse_json_response(response)
        
        return result
    
    def _call_ollama(self, prompt: str, num_predict: int = 18000) -> str:
        """Call Ollama and return response text."""
        response = ollama.chat(
            model=self.ollama_model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "num_ctx": 32768,
                "num_predict": num_predict,
                "temperature": 0,
            },
        )
        return response.message.content
    
    def _preprocess_text(self, text: str) -> str:
        """Preprocess text to reduce JSON quoting issues."""
        # Replace smart quotes with regular quotes
        text = text.replace('"', '"').replace('"', '"')
        text = text.replace(''', "'").replace(''', "'")
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        return text
    
    def _parse_json_response(self, response: str) -> Dict:
        """Parse JSON from LLM response with error handling."""
        response = response.strip()
        
        # Remove markdown code blocks
        if "```json" in response:
            start_marker = response.find("```json")
            end_marker = response.find("```", start_marker + 7)
            if end_marker > start_marker:
                response = response[start_marker + 7:end_marker]
        elif "```" in response:
            start_marker = response.find("```")
            end_marker = response.find("```", start_marker + 3)
            if end_marker > start_marker:
                response = response[start_marker + 3:end_marker]
        
        response = response.strip()
        
        # Find JSON start
        start = response.find("{")
        if start == -1:
            return {"error": "No JSON found", "raw": response[:3000]}
        
        json_str = response[start:]
        
        # Apply Ollama-specific fixes
        json_str = self._repair_double_quotes(json_str)
        
        # Try to parse directly
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        # Try to repair
        json_str = self._repair_json(json_str)
        
        try:
            result = json.loads(json_str)
            result["_truncated"] = True
            return result
        except json.JSONDecodeError:
            pass
        
        # Extract first complete JSON object
        extracted = self._extract_first_json_object(json_str)
        if extracted:
            try:
                result = json.loads(extracted)
                result["_truncated"] = True
                return result
            except json.JSONDecodeError:
                pass
        
        return {"error": "JSON parse failed", "raw": json_str[:3000]}
    
    def _repair_double_quotes(self, s: str) -> str:
        """Fix Ollama's pattern where values start with double quotes."""
        # Pattern: ": "" followed by actual text -> ": "text
        s = re.sub(r'": ""([^",}\]\n])', r'": "\1', s)
        return s
    
    def _extract_first_json_object(self, s: str) -> Optional[str]:
        """Extract the first complete JSON object from a string."""
        depth = 0
        start = s.find("{")
        if start == -1:
            return None
        
        in_string = False
        escape = False
        
        for i in range(start, len(s)):
            c = s[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
        
        return None
    
    def _repair_json(self, s: str) -> str:
        """Attempt to repair malformed JSON."""
        # Remove trailing commas
        s = re.sub(r",\s*([}\]])", r"\1", s)
        
        # Fix truncation - count brackets
        in_string = False
        escape = False
        open_braces = 0
        open_brackets = 0
        last_complete_pos = 0
        
        for i, c in enumerate(s):
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
                if open_braces >= 0 and open_brackets >= 0:
                    last_complete_pos = i + 1
            elif c == "[":
                open_brackets += 1
            elif c == "]":
                open_brackets -= 1
                if open_braces >= 0 and open_brackets >= 0:
                    last_complete_pos = i + 1
        
        # If we ended inside a string, try to close it
        if in_string:
            # Find a reasonable place to truncate - last complete object
            s = s.rstrip()
            # Try to close the string
            s += '"'
            in_string = False
        
        # Close unclosed brackets/braces
        s = s.rstrip()
        if s.endswith(","):
            s = s[:-1]
        
        # Recalculate after string fix
        open_braces = s.count("{") - s.count("}")
        open_brackets = s.count("[") - s.count("]")
        
        s += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        
        return s
    
    def extract(self, poster_path: str) -> Dict:
        """
        Extract structured JSON from a poster file.
        
        Handles GPU memory properly by:
        1. Extracting text (may load Qwen2-VL for images)
        2. UNLOADING vision model to free GPU memory
        3. THEN calling Ollama Llama 3.1 for JSON structuring
        
        Args:
            poster_path: Path to PDF or image file
            
        Returns:
            Extracted JSON dictionary with poster content
        """
        log(f"Extracting: {poster_path}")
        
        # Get raw text (may use vision model for images)
        raw_text, source = self.get_raw_text(poster_path)
        
        # CRITICAL: Unload vision model BEFORE calling Ollama to free GPU memory
        # This ensures Qwen2-VL and Llama 3.1 don't compete for GPU resources
        if self._vision_ocr is not None and self._vision_ocr._loaded:
            log("   Unloading vision model before JSON structuring...")
            self._vision_ocr.unload()
        
        if not raw_text or source == "unknown":
            return {"error": "Failed to extract text"}
        
        log(f"   Extracted {len(raw_text)} chars using {source}")
        
        # Structure to JSON using Ollama (now has full GPU available)
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

