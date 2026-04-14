#!/usr/bin/env python3
"""convert-test.py — Convert starry_test.h format to upstream test_framework.h format.

Reads a test file using the local starry_test.h harness (TEST/TEND blocks,
EXPECT_* macros) and converts it to the upstream linux-compatible-testsuit
format (test_framework.h with CHECK/CHECK_RET/CHECK_ERR, single main()).

Usage:
    convert-test.py <input.c> [output.c]

If output is not specified, prints to stdout.

This is a best-effort converter — complex tests may need manual adjustment.
Always verify the converted test compiles and passes on Linux before submitting.
"""
import re
import sys
from pathlib import Path

# Macro mappings: starry_test.h → test_framework.h
MACRO_MAP = {
    # EXPECT_EQ(val, expected) → CHECK((val) == (expected), "...")
    r'EXPECT_EQ\(([^,]+),\s*([^)]+)\)':
        lambda m: f'CHECK(({m.group(1).strip()}) == ({m.group(2).strip()}), "{m.group(1).strip()} == {m.group(2).strip()}")',

    # EXPECT_NE(val, bad) → CHECK((val) != (bad), "...")
    r'EXPECT_NE\(([^,]+),\s*([^)]+)\)':
        lambda m: f'CHECK(({m.group(1).strip()}) != ({m.group(2).strip()}), "{m.group(1).strip()} != {m.group(2).strip()}")',

    # EXPECT_TRUE(cond) → CHECK(cond, "...")
    r'EXPECT_TRUE\(([^)]+)\)':
        lambda m: f'CHECK({m.group(1).strip()}, "{m.group(1).strip()}")',

    # EXPECT_OK(val) → CHECK((val) >= 0, "...")
    r'EXPECT_OK\(([^)]+)\)':
        lambda m: f'CHECK(({m.group(1).strip()}) >= 0, "{m.group(1).strip()} >= 0")',

    # EXPECT_ERRNO(call, fail_val, errno) → CHECK_ERR(call, errno, "...")
    r'EXPECT_ERRNO\(([^,]+),\s*([^,]+),\s*([^)]+)\)':
        lambda m: f'CHECK_ERR({m.group(1).strip()}, {m.group(3).strip()}, "{m.group(1).strip()} should fail with {m.group(3).strip()}")',
}


def convert_file(input_text: str, test_name: str) -> str:
    lines = input_text.splitlines()
    output_lines = []
    in_test_block = False
    current_test_name = ""

    # Replace header include
    header_replaced = False

    for line in lines:
        # Replace starry_test.h include
        if '#include "starry_test.h"' in line:
            output_lines.append('#include "test_framework.h"')
            header_replaced = True
            continue

        # Remove TEST_BEGIN — we'll add TEST_START in main()
        if re.match(r'\s*TEST_BEGIN\s*\(', line):
            continue

        # Remove TEST_END — we'll add TEST_DONE in main()
        if re.match(r'\s*TEST_END\s*$', line):
            continue

        # Convert TEST("name") { → comment
        test_match = re.match(r'\s*TEST\s*\(\s*"([^"]+)"\s*\)\s*\{', line)
        if test_match:
            current_test_name = test_match.group(1)
            in_test_block = True
            output_lines.append(f'    /* --- {current_test_name} --- */')
            output_lines.append(f'    printf("  >> {current_test_name}\\n");')
            continue

        # Convert } TEND → nothing (just close the block comment)
        if re.match(r'\s*\}\s*TEND', line):
            in_test_block = False
            output_lines.append('')
            continue

        # Convert EXPECT_* macros
        converted = line
        for pattern, replacement in MACRO_MAP.items():
            converted = re.sub(pattern, replacement, converted)

        output_lines.append(converted)

    # Wrap in main() if not already present
    has_main = any('int main' in l for l in output_lines)
    if not has_main:
        # Find the first non-include, non-empty line after headers
        insert_idx = 0
        for i, line in enumerate(output_lines):
            if line.strip() and not line.strip().startswith('#') and not line.strip().startswith('//'):
                insert_idx = i
                break

        # Add main() wrapper
        pre = output_lines[:insert_idx]
        body = output_lines[insert_idx:]

        output_lines = pre + [
            '',
            f'int main(void) {{',
            f'    TEST_START("{test_name}");',
            '',
        ]

        # Indent body
        for line in body:
            if line.strip():
                output_lines.append('    ' + line)
            else:
                output_lines.append(line)

        output_lines.append('')
        output_lines.append('    TEST_DONE();')
        output_lines.append('}')

    return '\n'.join(output_lines) + '\n'


def main():
    if len(sys.argv) < 2:
        print("Usage: convert-test.py <input.c> [output.c]", file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    input_text = input_path.read_text()

    # Derive test name from filename
    test_name = input_path.stem
    if test_name.startswith('test_'):
        test_name = test_name[5:]

    result = convert_file(input_text, test_name)

    if output_path:
        output_path.write_text(result)
        print(f"[convert-test] Written to {output_path}", file=sys.stderr)
    else:
        print(result)


if __name__ == '__main__':
    main()
