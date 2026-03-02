import os
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = (
    "1"  # Transformers uses .isin for a simple op, which is not supported on MPS
)
import time
import json
import click
import base64
from io import BytesIO
from pathlib import Path
from PIL import Image
from marker.config.parser import ConfigParser
from marker.config.printer import CustomClickPrinter
from marker.logger import configure_logging, get_logger
from marker.models import create_model_dict

configure_logging()
logger = get_logger()

@click.command(cls=CustomClickPrinter, help="Convert a single PDF to JSON with extracted content.")
@click.argument("fpath", type=str)
@ConfigParser.common_options
def convert_single_cli(fpath: str, **kwargs):
    models = create_model_dict()
    start = time.time()
    
    config_parser = ConfigParser(kwargs)
    converter_cls = config_parser.get_converter_cls()
    converter = converter_cls(
        config=config_parser.generate_config_dict(),
        artifact_dict=models,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )
    
    # Convert the PDF
    rendered = converter(fpath)
    
    # Get file information
    file_path = Path(fpath)
    file_name = file_path.stem  # filename without extension
    
    # Helper function to convert image to base64
    def image_to_base64(img):
        if hasattr(img, 'save'):  # PIL Image object
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            img_str = base64.b64encode(buffer.getvalue()).decode()
            return {
                "format": "PNG",
                "data": img_str,
                "size": img.size if hasattr(img, 'size') else None
            }
        elif isinstance(img, str):  # If it's already a path or string
            return {"path": img}
        else:
            return {"error": "Unsupported image format"}
    
    # Create JSON structure
    result_data = {
        "file_name": file_name,
        "original_path": str(file_path),
        "content": rendered.markdown if hasattr(rendered, 'text') else str(rendered.markdown),
        "extraction_timestamp": time.time(),
        "processing_time": time.time() - start
    }
    
    # If there are images extracted, convert them to base64
    if hasattr(rendered, 'images') and rendered.images:
        serialized_images = []
        
        # Handle case where images is a dictionary
        if isinstance(rendered.images, dict):
            for img_name, img_obj in rendered.images.items():
                try:
                    serialized_img = image_to_base64(img_obj)
                    serialized_img["name"] = img_name
                    serialized_img["index"] = len(serialized_images)
                    serialized_images.append(serialized_img)
                except Exception as e:
                    logger.warning(f"Could not serialize image {img_name}: {e}")
                    serialized_images.append({
                        "name": img_name,
                        "index": len(serialized_images),
                        "error": str(e)
                    })
        # Handle case where images is a list
        else:
            for i, img in enumerate(rendered.images):
                try:
                    serialized_img = image_to_base64(img)
                    serialized_img["index"] = i
                    serialized_images.append(serialized_img)
                except Exception as e:
                    logger.warning(f"Could not serialize image {i}: {e}")
                    serialized_images.append({
                        "index": i,
                        "error": str(e)
                    })
        
        result_data["images"] = serialized_images
        result_data["images_count"] = len(serialized_images)
    
    # Save as JSON
    out_folder = config_parser.get_output_folder(fpath)
    os.makedirs(out_folder, exist_ok=True)
    
    json_filename = f"{config_parser.get_base_filename(fpath)}.json"
    json_path = os.path.join(out_folder, json_filename)
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Saved JSON to {json_path}")
    logger.info(f"Total time: {time.time() - start}")
    
    return result_data

if __name__ == "__main__":
    convert_single_cli([r"C:\Users\hp\Desktop\digex\TestSet\cc.png", "--extract_images", "yes", "--output_dir", "output_folder"])
