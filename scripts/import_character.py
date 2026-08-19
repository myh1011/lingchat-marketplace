#!/usr/bin/env python3
"""Import a character package from extracted files into the marketplace registry.

Usage:
    python3 scripts/import_character.py <pkg-id> <name> <source_dir> [--tags tag1,tag2] [--description "..."]
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

import yaml

MARKETPLACE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = MARKETPLACE_ROOT / "scripts"


def parse_settings_txt(text: str) -> dict:
    """Parse the custom settings.txt format into a dict."""
    import re
    result = {}
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            i += 1
            continue
        m = re.match(r'^(\w+)\s*=\s*(.*)', stripped)
        if not m:
            i += 1
            continue
        key = m.group(1)
        rest = m.group(2).strip()
        if rest.startswith('"""'):
            rest = rest[3:]
            end_idx = rest.find('"""')
            if end_idx != -1:
                value = rest[:end_idx]
                i += 1
            else:
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
        elif rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
            result[key] = rest[1:-1]
            i += 1
        elif rest.startswith('{'):
            collected = [rest]
            i += 1
            while i < len(lines):
                collected.append(lines[i])
                if '}' in lines[i]:
                    i += 1
                    break
                i += 1
            dict_text = '\n'.join(collected)
            try:
                import ast
                result[key] = ast.literal_eval(dict_text)
            except:
                result[key] = dict_text
        elif re.match(r'^-?\d+(\.\d+)?$', rest):
            result[key] = float(rest) if '.' in rest else int(rest)
            i += 1
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
    """Convert parsed settings to YAML format."""
    yml = {}
    mappings = {
        'ai_name': 'ai_name',
        'ai_subtitle': 'ai_subtitle',
        'user_name': 'user_name',
        'user_subtitle': 'user_subtitle',
        'title': 'title',
        'info': 'info',
        'scale': 'scale',
        'offset': 'offset',
        'bubble_top': 'bubble_top',
        'bubble_left': 'bubble_left',
        'thinking_message': 'thinking_message',
        'system_prompt': 'system_prompt',
        'voice_models': 'voice_models',
        'tts_type': 'tts_type',
    }
    for src, dst in mappings.items():
        if src in data:
            v = data[src]
            if isinstance(v, str):
                v = v.strip()
            yml[dst] = v
    if 'speaker_id' in data and 'voice_models' not in yml:
        yml['voice_models'] = {"sva_speaker_id": str(data['speaker_id'])}
    # Convert numeric types
    if 'scale' in yml:
        yml['scale'] = float(yml['scale'])
    if 'offset' in yml:
        yml['offset'] = float(yml['offset'])
    if 'bubble_top' in yml:
        yml['bubble_top'] = int(yml['bubble_top'])
    if 'bubble_left' in yml:
        yml['bubble_left'] = int(yml['bubble_left'])
    return yml


class LiteralStr(str):
    pass


def literal_str_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


yaml.add_representer(LiteralStr, literal_str_representer)


def convert_multiline(obj):
    if isinstance(obj, dict):
        return {k: convert_multiline(v) for k, v in obj.items()}
    elif isinstance(obj, str) and '\n' in obj:
        s = obj.strip('\n')
        return LiteralStr(s) if s else obj
    return obj


def main():
    ap = argparse.ArgumentParser(description="Import character package")
    ap.add_argument("pkg_id", help="Package ID (e.g. amiya)")
    ap.add_argument("name", help="Display name (e.g. 阿米娅)")
    ap.add_argument("source_dir", help="Extracted source directory")
    ap.add_argument("--tags", default="", help="Comma-separated tags")
    ap.add_argument("--description", default="", help="Short description")
    ap.add_argument("--author", default="LingChat 创意工坊", help="Author name")
    ap.add_argument("--version", default="0.1.0", help="Version string")
    args = ap.parse_args()

    src = Path(args.source_dir)
    registry_dir = MARKETPLACE_ROOT / "registry" / args.pkg_id
    payload_dir = registry_dir / "payload"

    if registry_dir.exists():
        print(f"Removing existing: {registry_dir}")
        shutil.rmtree(registry_dir)
    payload_dir.mkdir(parents=True)

    # Find and convert settings.txt
    settings_txt = None
    for f in src.rglob("settings.txt"):
        if f.name == "settings.txt":
            settings_txt = f
            break

    if settings_txt:
        text = settings_txt.read_text(encoding='utf-8')
        data = parse_settings_txt(text)
        yml_data = convert_to_yml(data)
        yml_data = convert_multiline(yml_data)

        # Write settings.yml
        with open(payload_dir / "settings.yml", 'w', encoding='utf-8') as f:
            yaml.dump(yml_data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        print(f"Created settings.yml from {settings_txt}")

        # Copy other files from the same directory as settings.txt
        settings_dir = settings_txt.parent
        for f in settings_dir.iterdir():
            if f.name == "settings.txt":
                continue
            if f.is_dir():
                shutil.copytree(f, payload_dir / f.name)
            else:
                shutil.copy2(f, payload_dir / f.name)
        print(f"Copied payload files from {settings_dir}")
    else:
        # No settings.txt found - just copy everything
        for f in src.iterdir():
            if f.is_dir():
                shutil.copytree(f, payload_dir / f.name)
            else:
                shutil.copy2(f, payload_dir / f.name)
        print(f"No settings.txt found, copied all files from {src}")

    # Also copy avatar files if they're at root level
    for f in src.iterdir():
        if f.is_file() and f.suffix in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
            shutil.copy2(f, payload_dir / f.name)
    for d in src.iterdir():
        if d.is_dir() and d.name == 'avatar':
            shutil.copytree(d, payload_dir / 'avatar', dirs_exist_ok=True)

    # Create manifest.toml
    tags = [t.strip() for t in args.tags.split(',') if t.strip()]
    manifest = f'''id = "{args.pkg_id}"
name = "{args.name}"
type = "character"
version = "{args.version}"
author = "{args.author}"
description = "{args.description or args.name + '（创意工坊分享）'}"

[content]
category = "角色卡"
tags = {tags!r}
'''
    manifest_path = registry_dir / "manifest.toml"
    manifest_path.write_text(manifest, encoding='utf-8')
    print(f"Created manifest.toml")

    print(f"\nPackage '{args.pkg_id}' imported to {registry_dir}")


if __name__ == '__main__':
    sys.exit(main() or 0)
