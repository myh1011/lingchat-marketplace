#!/usr/bin/env python3
"""Convert settings.txt (LingChat custom format) to settings.yml (YAML).

Usage:
    python3 scripts/convert_settings.py <input_settings.txt> <output_settings.yml>
"""

import re
import sys
from pathlib import Path

import yaml


def parse_settings_txt(text: str) -> dict:
    """Parse the custom settings.txt format into a dict."""
    result = {}
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith('#'):
            i += 1
            continue

        # Match key = value
        m = re.match(r'^(\w+)\s*=\s*(.*)', stripped)
        if not m:
            i += 1
            continue

        key = m.group(1)
        rest = m.group(2).strip()

        # Triple-quoted multi-line string
        if rest.startswith('"""'):
            # Remove opening """
            rest = rest[3:]
            # Check if it's a single-line triple-quoted string
            end_idx = rest.find('"""')
            if end_idx != -1:
                value = rest[:end_idx]
                i += 1
            else:
                # Multi-line
                value_lines = [rest]
                i += 1
                while i < len(lines):
                    line = lines[i]
                    end_idx = line.find('"""')
                    if end_idx != -1:
                        value_lines.append(line[:end_idx])
                        i += 1
                        break
                    value_lines.append(line)
                    i += 1
                value = '\n'.join(value_lines)
            result[key] = value
        # Single-quoted string
        elif rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
            result[key] = rest[1:-1]
            i += 1
        # Dict like {...}
        elif rest.startswith('{'):
            # Collect until closing }
            collected = [rest]
            i += 1
            while i < len(lines):
                collected.append(lines[i])
                if '}' in lines[i]:
                    i += 1
                    break
                i += 1
            dict_text = '\n'.join(collected)
            # Try to parse as Python dict
            try:
                import ast
                result[key] = ast.literal_eval(dict_text)
            except:
                result[key] = dict_text
        # Number
        elif re.match(r'^-?\d+(\.\d+)?$', rest):
            result[key] = float(rest) if '.' in rest else int(rest)
            i += 1
        # Boolean
        elif rest.lower() == 'true':
            result[key] = True
            i += 1
        elif rest.lower() == 'false':
            result[key] = False
            i += 1
        else:
            result[key] = rest
            i += 1

    return result


def convert_to_yml(data: dict) -> dict:
    """Convert parsed settings to the YAML format expected by the installer."""
    yml = {}

    # Map fields
    if 'ai_name' in data:
        yml['ai_name'] = data['ai_name']
    if 'ai_subtitle' in data:
        yml['ai_subtitle'] = data['ai_subtitle']
    if 'user_name' in data:
        yml['user_name'] = data['user_name']
    if 'user_subtitle' in data:
        yml['user_subtitle'] = data['user_subtitle']
    if 'title' in data:
        yml['title'] = data['title']
    if 'info' in data:
        yml['info'] = data['info']
    if 'scale' in data:
        yml['scale'] = float(data['scale'])
    if 'offset' in data:
        yml['offset'] = float(data['offset'])
    if 'bubble_top' in data:
        yml['bubble_top'] = int(data['bubble_top'])
    if 'bubble_left' in data:
        yml['bubble_left'] = int(data['bubble_left'])
    if 'thinking_message' in data:
        yml['thinking_message'] = data['thinking_message']
    if 'system_prompt' in data:
        yml['system_prompt'] = data['system_prompt']
    if 'voice_models' in data:
        yml['voice_models'] = data['voice_models']
    if 'speaker_id' in data:
        # Convert to voice_models format
        yml['voice_models'] = {"sva_speaker_id": str(data['speaker_id'])}
    if 'tts_type' in data:
        yml['tts_type'] = data['tts_type']

    return yml


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_settings.txt> <output_settings.yml>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    text = input_path.read_text(encoding='utf-8')
    data = parse_settings_txt(text)
    yml_data = convert_to_yml(data)

    # Write YAML with proper formatting
    class LiteralStr(str):
        pass

    def literal_str_representer(dumper, data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')

    yaml.add_representer(LiteralStr, literal_str_representer)

    # Convert multi-line strings to LiteralStr
    def convert_multiline(obj):
        if isinstance(obj, dict):
            return {k: convert_multiline(v) for k, v in obj.items()}
        elif isinstance(obj, str) and '\n' in obj:
            s = obj.strip('\n')
            return LiteralStr(s) if s else obj
        return obj

    yml_data = convert_multiline(yml_data)

    with open(output_path, 'w', encoding='utf-8') as f:
        yaml.dump(yml_data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"Converted: {input_path} -> {output_path}")


if __name__ == '__main__':
    main()
