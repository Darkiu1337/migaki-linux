#!/usr/bin/env python3
"""Generate GLSL compute shaders from the Magpie CuNNy 4x16-NVL HLSL model.

The HLSL is a linear sequence of //!PASS blocks.  Each conv pass declares
input textures (INPUT or T0..T7), accumulates `r<n> += mul(s<tex>_<row>_<col>, M4(...))`
terms and finishes with an optional bias, an optional ReLU and one or more
texture stores.  Variables named s<tex>_<row>_<col> are reused for every input
texture, so the (texture, offset) pair that applies to a `mul` is whatever was
assigned to that name most recently.

HLSL `mul(vecN, M)` with a row-major literal is exactly GLSL `mat * vecN` with
the same literal order (GLSL columns == HLSL rows), so the weight literals are
emitted verbatim.

Usage: gen_cunny.py [path/to/CuNNy-4x16-NVL.hlsl]
Writes cs_cunny_in.comp, cs_cunny_conv1..4.comp and cs_cunny_out.comp next to
this script.  Exits non-zero if the model does not match the expected shape.
"""

import math
import os
import re
import sys

HEADER = """// Generated from CuNNy-4x16-NVL.hlsl (Blinue/CuNNy @ 5724c10e267fcfb19d4a92384e26d813f6c3e648).
// LGPL-3.0/GPL-3.0 upstream; see licence.txt.
"""

PREAMBLE = """#version 450
#extension GL_EXT_scalar_block_layout : require

layout(local_size_x = 8, local_size_y = 8, local_size_z = 1) in;

layout(binding = 0, scalar) uniform layers_t {
    uvec2 u_inputSize;
    uvec2 u_outputSize;
};
"""

RE_L_PASS = re.compile(r'^//!PASS\s+(\d+)')
RE_L_DESC = re.compile(r'^//!DESC\s+(.*)$')
RE_L_IN = re.compile(r'^//!IN\s+(.*)$')
RE_L_OUT = re.compile(r'^//!OUT\s+(.*)$')
RE_DEFINE = re.compile(r'^#define\s+L(\d+)\(x,\s*y\)\s*V(\d)\(O\((\w+),\s*x,\s*y\)(?:\.rgb)?\)')
RE_ASSIGN = re.compile(r's(\d+)_(-?\d+)_(-?\d+)\s*=\s*L(\d+)\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)')
RE_MUL = re.compile(r'r(\d+)\s*\+=\s*mul\(s(\d+)_(-?\d+)_(-?\d+),\s*(M4|M3x4)\(([^)]*)\)\)')
RE_BIAS = re.compile(r'r(\d+)\s*\+=\s*V(\d)\(([^)]*)\)')
RE_RELU = re.compile(r'r(\d+)\s*=\s*max\(r\d+,\s*0\.0\)')


class Pass:
    def __init__(self, number):
        self.number = number
        self.desc = ''
        self.inputs = []
        self.outputs = []
        self.lmap = {}          # L index -> texture name
        self.current = {}       # s<tex>_<row>_<col> -> (texture, dx, dy)
        self.terms = {}         # group -> [(samplerIdx, dx, dy, kind, values)]
        self.bias = {}          # group -> [values]
        self.relu = set()

    def sampler_index(self, texture):
        if texture not in self.inputs:
            self.inputs.append(texture)
        return self.inputs.index(texture)


def parse(path):
    passes = []
    current = None
    with open(path, 'r', encoding='utf-8') as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith('//!'):
                m = RE_L_PASS.match(line)
                if m:
                    current = Pass(int(m.group(1)))
                    passes.append(current)
                    continue
                if current is None:
                    continue
                m = RE_L_DESC.match(line)
                if m:
                    current.desc = m.group(1).strip()
                    continue
                m = RE_L_IN.match(line)
                if m:
                    continue
                m = RE_L_OUT.match(line)
                if m:
                    current.outputs = [t.strip() for t in m.group(1).split(',')]
                    continue
                continue
            if current is None:
                continue

            m = RE_DEFINE.match(line)
            if m:
                current.lmap[int(m.group(1))] = m.group(3)
                continue

            for m in RE_ASSIGN.finditer(line):
                key = 's%s_%s_%s' % (m.group(1), m.group(2), m.group(3))
                texture = current.lmap[int(m.group(4))]
                dx = int(round(float(m.group(5))))
                dy = int(round(float(m.group(6))))
                current.current[key] = (texture, dx, dy)
                # Keep the texture referenced even if it is never multiplied.
                current.sampler_index(texture)
                continue

            done = False
            for m in RE_MUL.finditer(line):
                key = 's%s_%s_%s' % (m.group(2), m.group(3), m.group(4))
                if key not in current.current:
                    raise SystemExit('pass %d: mul uses unassigned %s' % (current.number, key))
                texture, dx, dy = current.current[key]
                kind = m.group(5)
                values = [v.strip() for v in m.group(6).split(',')]
                expected = 12 if kind == 'M3x4' else 16
                if len(values) != expected:
                    raise SystemExit('pass %d: %s has %d values' % (current.number, kind, len(values)))
                current.terms.setdefault(int(m.group(1)), []).append(
                    (current.sampler_index(texture), dx, dy, kind, values))
                done = True
            if done:
                continue

            m = RE_BIAS.search(line)
            if m:
                values = [v.strip() for v in m.group(3).split(',')]
                current.bias[int(m.group(1))] = values
                continue

            m = RE_RELU.match(line)
            if m:
                current.relu.add(int(m.group(1)))
                continue

    return passes


def relu_expr(name):
    return '    %s = max(%s, vec4(0.0));\n' % (name, name)


def gen_in(p, path):
    groups = sorted(p.terms)
    assert groups == [0, 1, 2, 3], 'pass 1 groups %r' % groups
    assert list(p.inputs) == ['INPUT'], 'pass 1 inputs %r' % p.inputs
    out = [HEADER, PREAMBLE]
    for i, binding in enumerate((1, 2, 7, 8)):
        out.append('layout(binding = %d, rgba16f) writeonly uniform image2D dst%d;\n' % (binding, i))
    out.append('''
layout(binding = 3) uniform sampler2D s_samplers[16];

vec3 fetchTex(int i, ivec2 c, ivec2 o) {
    ivec2 p = clamp(c + o, ivec2(0), ivec2(u_inputSize) - 1);
    return texelFetch(s_samplers[i], p, 0).rgb;
}

void main() {
    ivec2 c = ivec2(gl_GlobalInvocationID.xy);
    if (c.x >= int(u_inputSize.x) || c.y >= int(u_inputSize.y)) return;
''')
    for group in groups:
        name = 'r%d' % group
        out.append('    vec4 %s = vec4(0.0);\n' % name)
        for sampler, dx, dy, kind, values in p.terms[group]:
            assert kind == 'M3x4'
            out.append('    %s += mat3x4(%s) * fetchTex(%d, c, ivec2(%d, %d));\n'
                       % (name, ', '.join(values), sampler, dx, dy))
        if group in p.bias:
            out.append('    %s += vec4(%s);\n' % (name, ', '.join(p.bias[group])))
        if group in p.relu:
            out.append(relu_expr(name))
        out.append('    imageStore(dst%d, c, %s);\n' % (group, name))
    out.append('}\n')
    write(path, out)


def gen_conv(p, path, index):
    groups = sorted(p.terms)
    assert groups == [0, 1, 2, 3], 'pass %d groups %r' % (p.number, groups)
    assert len(p.inputs) == 4, 'pass %d inputs %r' % (p.number, p.inputs)
    out = [HEADER, PREAMBLE]
    for i, binding in enumerate((1, 2, 7, 8)):
        out.append('layout(binding = %d, rgba16f) writeonly uniform image2D dst%d;\n' % (binding, i))
    out.append('''
layout(binding = 3) uniform sampler2D s_samplers[16];

vec4 fetchTex(int i, ivec2 c, ivec2 o) {
    ivec2 p = clamp(c + o, ivec2(0), ivec2(u_inputSize) - 1);
    return texelFetch(s_samplers[i], p, 0);
}

void main() {
    ivec2 c = ivec2(gl_GlobalInvocationID.xy);
    if (c.x >= int(u_inputSize.x) || c.y >= int(u_inputSize.y)) return;
''')
    for group in groups:
        name = 'r%d' % group
        out.append('    vec4 %s = vec4(0.0);\n' % name)
        for sampler, dx, dy, kind, values in p.terms[group]:
            assert kind == 'M4'
            out.append('    %s += mat4(%s) * fetchTex(%d, c, ivec2(%d, %d));\n'
                       % (name, ', '.join(values), sampler, dx, dy))
        if group in p.bias:
            out.append('    %s += vec4(%s);\n' % (name, ', '.join(p.bias[group])))
        if group in p.relu:
            out.append(relu_expr(name))
        out.append('    imageStore(dst%d, c, %s);\n' % (group, name))
    out.append('}\n')
    write(path, out)


def gen_out(p, path):
    groups = sorted(p.terms)
    assert groups == [0, 1, 2], 'out-shuffle groups %r' % groups
    assert p.inputs == ['T0', 'T1', 'T2', 'T3'], 'out-shuffle inputs %r' % p.inputs
    # The out-shuffle fetches the untouched original through a fifth sampler.
    original_sampler = len(p.inputs)

    out = [HEADER, PREAMBLE]
    out.append('layout(binding = 1, rgba8) writeonly uniform image2D dst;\n')
    out.append('''
layout(binding = 3) uniform sampler2D s_samplers[16];

vec4 fetchTex(int i, ivec2 c, ivec2 o) {
    ivec2 p = clamp(c + o, ivec2(0), ivec2(u_inputSize) - 1);
    return texelFetch(s_samplers[i], p, 0);
}

void main() {
    ivec2 c = ivec2(gl_GlobalInvocationID.xy);
    if (c.x >= int(u_inputSize.x) || c.y >= int(u_inputSize.y)) return;
''')
    for group in groups:
        name = 'r%d' % group
        out.append('    vec4 %s = vec4(0.0);\n' % name)
        for sampler, dx, dy, kind, values in p.terms[group]:
            assert kind == 'M4'
            out.append('    %s += mat4(%s) * fetchTex(%d, c, ivec2(%d, %d));\n'
                       % (name, ', '.join(values), sampler, dx, dy))
    out.append('''
    for (int k = 0; k < 4; k++) {
        ivec2 oc = c * 2 + ivec2(k & 1, k >> 1);
        vec2 uv = (vec2(oc) + vec2(0.5)) / vec2(u_outputSize);
        vec3 orig = texture(s_samplers[%d], uv).rgb;
        vec3 v = orig + vec3(r0[k], r1[k], r2[k]);
        imageStore(dst, oc, vec4(clamp(v, 0.0, 1.0), 1.0));
    }
}
''' % original_sampler)
    write(path, out)


def write(path, lines):
    with open(path, 'w', encoding='utf-8') as handle:
        handle.writelines(lines)
    print('wrote %s' % path)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    source = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, 'CuNNy-4x16-NVL.hlsl')
    passes = parse(source)
    if len(passes) != 6:
        raise SystemExit('expected 6 passes, got %d' % len(passes))

    total = 0
    for p in passes:
        muls = sum(len(v) for v in p.terms.values())
        total += muls
        print('pass %d %-14s inputs=%-28s muls=%3d bias=%d relu=%d out=%s'
              % (p.number, p.desc, ','.join(p.inputs), muls, len(p.bias),
                 len(p.relu), ','.join(p.outputs)))
    print('total muls = %d' % total)

    expected = [
        (1, 36, 4, 4),
        (2, 144, 0, 4),
        (3, 144, 0, 4),
        (4, 144, 0, 4),
        (5, 144, 0, 4),
        (6, 108, 0, 0),
    ]
    for p, (number, muls, biases, relus) in zip(passes, expected):
        assert p.number == number
        got = (sum(len(v) for v in p.terms.values()), len(p.bias), len(p.relu))
        if got != (muls, biases, relus):
            raise SystemExit('pass %d: expected (muls, bias, relu)=%r, got %r'
                             % (number, (muls, biases, relus), got))
    assert total == 720, 'expected 720 muls, got %d' % total

    gen_in(passes[0], os.path.join(here, 'cs_cunny_in.comp'))
    for i in range(4):
        gen_conv(passes[1 + i], os.path.join(here, 'cs_cunny_conv%d.comp' % (i + 1)), i + 1)
    gen_out(passes[5], os.path.join(here, 'cs_cunny_out.comp'))


if __name__ == '__main__':
    main()
