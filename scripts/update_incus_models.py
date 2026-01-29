#!/usr/bin/env python3
"""
Fetch Incus OpenAPI spec and generate Pydantic models.

This script:
1. Fetches the Incus Swagger 2.0 spec from linuxcontainers.org
2. Converts it to OpenAPI 3.0 using converter.swagger.io
3. Generates Pydantic v2 models using datamodel-codegen

Usage:
    python scripts/update_incus_models.py

The generated models are written to src/incus/models.py
"""

import sys
from pathlib import Path

import httpx
import yaml
from datamodel_code_generator import generate
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.enums import InputFileType, DataModelType

# Paths relative to this script
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
OPENAPI_SPEC = PROJECT_ROOT / "data" / "openapi.yaml"
MODELS_OUTPUT = PROJECT_ROOT / "src" / "cli" / "models_generated.py"

# URLs
INCUS_SWAGGER_URL = "https://linuxcontainers.org/incus/docs/main/rest-api.yaml"
SWAGGER_CONVERTER_API = "https://converter.swagger.io/api/convert"


def fetch_swagger_spec() -> dict:
    """Fetch the Incus Swagger 2.0 spec."""
    print(f"Fetching {INCUS_SWAGGER_URL}...")
    response = httpx.get(INCUS_SWAGGER_URL, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    spec = yaml.safe_load(response.text)
    print(f"  ✓ Fetched Swagger {spec.get('swagger', '2.0')} spec")
    return spec


def convert_to_openapi3(swagger_spec: dict) -> dict:
    """Convert Swagger 2.0 to OpenAPI 3.0 using converter.swagger.io."""
    print("Converting to OpenAPI 3.0 via converter.swagger.io...")
    response = httpx.post(
        SWAGGER_CONVERTER_API,
        json=swagger_spec,
        headers={"Content-Type": "application/json"},
        timeout=60.0,
    )
    response.raise_for_status()
    openapi3 = response.json()
    print(f"  ✓ Converted to OpenAPI {openapi3.get('openapi', '3.x')}")
    return openapi3


def save_openapi_spec(spec: dict, path: Path) -> None:
    """Save the OpenAPI spec to a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"  ✓ Saved {path}")


def generate_models(openapi_path: Path, output_path: Path) -> None:
    """Generate Pydantic models using datamodel-codegen."""
    print("Generating Pydantic models...")
    
    generate(
        input_=openapi_path,
        input_file_type=InputFileType.OpenAPI,
        output_model_type=DataModelType.PydanticV2BaseModel,
        output=output_path,
        formatters=[Formatter.BLACK, Formatter.ISORT],
    )
    
    # Count lines in generated file
    lines = output_path.read_text().count("\n")
    print(f"  ✓ Generated {output_path} ({lines} lines)")


def main() -> int:
    print("=" * 60)
    print("Updating Incus API models")
    print("=" * 60)
    
    try:
        # Step 1: Fetch
        swagger_spec = fetch_swagger_spec()
        
        # Step 2: Convert
        openapi_spec = convert_to_openapi3(swagger_spec)
        
        # Step 3: Save OpenAPI spec (useful for reference)
        save_openapi_spec(openapi_spec, OPENAPI_SPEC)
        
        # Step 4: Generate models
        generate_models(OPENAPI_SPEC, MODELS_OUTPUT)
        
        print("=" * 60)
        print("✓ Done! Models updated successfully.")
        print("=" * 60)
        return 0
        
    except httpx.HTTPError as e:
        print(f"✗ HTTP error: {e}")
        return 1
    except Exception as e:
        print(f"✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
