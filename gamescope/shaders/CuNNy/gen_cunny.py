#!/usr/bin/env python3
"""Generate GLSL compute shaders from a Magpie CuNNy HLSL model.

The HLSL is a linear sequence of //!PASS blocks. Each conv pass declares
input textures (INPUT or T0..T7), accumulates `r<n> += mul(s<tex>_<row>_<col>, M4(...))`
terms and finishes with an optional bias, an optional ReLU and one or more
texture stores. Variables named s<tex>_<row>_<col> are reused for every input
texture, so the (texture, offset) pair that applies to a `mul` is whatever was
assigned to that name most recently.

HLSL `mul(vecN, M)` with a row-major literal is exactly GLSL `mat * vecN` with
the same literal order (GLSL columns == HLSL rows), so the weight literals are
emitted verbatim.

The channel width comes from the model itself: pass 1 stores one RGBA texture
per four feature channels, and every later pass reads and writes that same
number of targets. The storage-image bindings are the ones gamescope's
descriptor set reserves for targets (1, 2, 7, 8, 9, 10, 11, 12), so a 12-, 16-,
24- or 32-channel model needs 3, 4, 6 or 8 of them and nothing else changes.

Usage: gen_cunny.py [--model NAME] [--out DIR] [path/to/CuNNy-<model>.hlsl]
Writes cs_cunny_<model>_in.comp, cs_cunny_<model>_convN.comp and
cs_cunny_<model>_out.comp into DIR (default: a directory next to this script
named after the model). The model token is in the file name because gamescope's
build names each generated shader header after its source stem.
Exits non-zero if the model does not match the expected shape.
"""

import math
import os
import re
import sys

# gamescope's target bindings, in the order the host binds them
# (src/shaders/descriptor_set_constants.h VKR_TARGET_SLOTS).
TARGET_BINDINGS = (1, 2, 7, 8, 9, 10, 11, 12)

HEADER = """// Generated from CuNNy-%s.hlsl (Blinue/CuNNy @ 5724c10e267fcfb19d4a92384e26d813f6c3e648).
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
        self.inputs = []        # textures in declaration order
        self.outputs = []       # declared //!OUTPUT names
        self.lmap = {}          # #define index -> texture name
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


def target_decls(slots):
    """One writeonly storage image per RGBA target, at gamescope's bindings."""
    if slots > len(TARGET_BINDINGS):
        raise SystemExit('%d targets do not fit the %d reserved bindings'
                         % (slots, len(TARGET_BINDINGS)))
    return ['layout(binding = %d, rgba16f) writeonly uniform image2D dst%d;\n'
            % (binding, i) for i, binding in enumerate(TARGET_BINDINGS[:slots])]


def gen_in(p, path, slots, header):
    groups = sorted(p.terms)
    if groups != list(range(slots)):
        raise SystemExit('pass 1 writes groups %r, expected 0..%d' % (groups, slots - 1))
    if list(p.inputs) != ['INPUT']:
        raise SystemExit('pass 1 inputs %r' % p.inputs)
    out = [header, PREAMBLE]
    out.extend(target_decls(slots))
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


def gen_conv(p, path, slots, header):
    groups = sorted(p.terms)
    if groups != list(range(slots)):
        raise SystemExit('pass %d writes groups %r, expected 0..%d'
                         % (p.number, groups, slots - 1))
    if len(p.inputs) != slots:
        raise SystemExit('pass %d reads %d textures, expected %d'
                         % (p.number, len(p.inputs), slots))
    out = [header, PREAMBLE]
    out.extend(target_decls(slots))
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


def gen_out(p, path, slots, header):
    groups = sorted(p.terms)
    if groups != [0, 1, 2]:
        raise SystemExit('out-shuffle groups %r' % groups)
    if len(p.inputs) != slots:
        raise SystemExit('out-shuffle reads %d textures, expected %d'
                         % (len(p.inputs), slots))
    # The out-shuffle fetches the untouched original through the next sampler.
    original_sampler = slots

    out = [header, PREAMBLE]
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


def model_name(source):
    """CuNNy-4x16-NVL.hlsl -> 4x16-NVL."""
    stem = os.path.splitext(os.path.basename(source))[0]
    return stem[len('CuNNy-'):] if stem.startswith('CuNNy-') else stem


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    args = sys.argv[1:]
    model = None
    outdir = None
    while args and args[0].startswith('--'):
        if args[0] == '--model':
            model = args[1]
            del args[:2]
        elif args[0] == '--out':
            outdir = args[1]
            del args[:2]
        else:
            raise SystemExit('unknown option %r' % args[0])
    source = args[0] if args else None
    model = model or (model_name(source) if source else '4x16-NVL')
    source = source or os.path.join(here, 'CuNNy-%s.hlsl' % model)
    outdir = outdir or os.path.join(here, model)

    passes = parse(source)
    if len(passes) < 3:
        raise SystemExit('expected an input pass, conv passes and an out pass, got %d'
                         % len(passes))
    slots = len(set(passes[0].terms))
    # Pass 1 expands RGB into the model's channels, the last pass is the
    # out-shuffle (always r0..r2), everything in between is a conv pass.
    conv = passes[1:-1]
    if not conv:
        raise SystemExit('no conv passes found')
    total = 0
    for p in passes:
        muls = sum(len(v) for v in p.terms.values())
        total += muls
        print('pass %d %-14s inputs=%-28s muls=%3d bias=%d relu=%d out=%s'
              % (p.number, p.desc, ','.join(p.inputs), muls, len(p.bias),
                 len(p.relu), ','.join(p.outputs)))
    print('model %s: %d passes, %d targets, total muls = %d'
          % (model, len(passes), slots, total))
    if slots < 1 or slots > len(TARGET_BINDINGS):
        raise SystemExit('%d targets do not fit the reserved bindings' % slots)

    os.makedirs(outdir, exist_ok=True)
    header = HEADER % model
    # The file name is the GLSL->header stem gamescope's build generates, so it
    # carries the model: four models cannot all ship `cs_cunny_in`.
    prefix = 'cs_cunny_%s_' % model.lower().replace('-nvl', '').replace('-', '')
    gen_in(passes[0], os.path.join(outdir, prefix + 'in.comp'), slots, header)
    for index, p in enumerate(conv):
        gen_conv(p, os.path.join(outdir, prefix + 'conv%d.comp' % (index + 1)),
                 slots, header)
    gen_out(passes[-1], os.path.join(outdir, prefix + 'out.comp'), slots, header)


if __name__ == '__main__':
    main()
