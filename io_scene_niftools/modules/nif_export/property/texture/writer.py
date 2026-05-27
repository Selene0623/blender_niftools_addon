"""This script contains helper methods to export textures sources."""

# ***** BEGIN LICENSE BLOCK *****
# 
# Copyright © 2013, NIF File Format Library and Tools contributors.
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
# 
#    * Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials provided
#      with the distribution.
# 
#    * Neither the name of the NIF File Format Library and Tools
#      project nor the names of its contributors may be used to endorse
#      or promote products derived from this software without specific
#      prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# ***** END LICENSE BLOCK *****

import os.path
import struct
import numpy as np

import bpy
from nifgen.formats.nif import classes as NifClasses

import io_scene_niftools.utils.logging
from io_scene_niftools.modules.nif_export.block_registry import block_store
from io_scene_niftools.utils import math
from io_scene_niftools.utils.singleton import NifOp
from io_scene_niftools.utils.logging import NifLog
from io_scene_niftools.utils.singleton import NifData


class TextureWriter:
    _embed_cache = {}
    # Maps a computed source-key to the already-built NiSourceTexture block.
    _source_texture_key_map = {}
    # Reverse map to recover the key for a block (debugging/consistency).
    _source_texture_block_key = {}

    @staticmethod
    def clear_embed_cache():
        TextureWriter._embed_cache = {}
        TextureWriter._source_texture_key_map = {}
        TextureWriter._source_texture_block_key = {}

    @staticmethod
    def _build_source_texture_key(srctex, source_path=None, target_fourcc=None):
        # Build a stable tuple that describes the exported texture so identical
        # sources reuse the same NiSourceTexture block.
        fmt = getattr(srctex, "format_prefs", None)
        pixel_layout = getattr(fmt, "pixel_layout", None)
        use_mipmaps = getattr(fmt, "use_mipmaps", None)
        alpha_format = getattr(fmt, "alpha_format", None)
        is_static = getattr(srctex, "is_static", None)
        file_name = getattr(srctex, "file_name", None)
        use_external = bool(getattr(srctex, "use_external", False))
        if use_external:
            return ("external", file_name, pixel_layout, use_mipmaps, alpha_format, is_static)
        embed_only_base = getattr(NifOp.props, "embed_only_base_mipmap", True)
        source_key = source_path or file_name
        return ("embed", source_key, file_name, target_fourcc, embed_only_base, pixel_layout, use_mipmaps, alpha_format, is_static)

    @staticmethod
    def _register_source_texture_key(block, key):
        # Store both directions so we can dedupe by key and introspect later.
        TextureWriter._source_texture_key_map[key] = block
        TextureWriter._source_texture_block_key[block] = key

    @staticmethod
    def get_source_texture_key_for_block(block):
        return TextureWriter._source_texture_block_key.get(block)

    @staticmethod
    def export_source_texture(n_texture=None, filename=None):
        """Export a NiSourceTexture.

        :param n_texture: The n_texture object in blender to be exported.
        :param filename: The full or relative path to the n_texture file
            (this argument is used when exporting NiFlipControllers
            and when exporting default shader slots that have no use in
            being imported into Blender).
        :param b_mat: Optional blender material to check for export format overrides.
        :return: The exported NiSourceTexture block.
        """

        # create NiSourceTexture
        srctex = NifClasses.NiSourceTexture(NifData.data)
        # Determine external vs embedded based on export option
        embed = getattr(NifOp.props, 'embed_textures', False)
        srctex.use_external = not embed
        
        # Set file_name property
        if filename is not None:
            # preset filename
            srctex.file_name = filename
        elif n_texture is not None:
            # Keep filename even when embedding; engines typically ignore it when use_external=False,
            # but tools may display it and some pipelines expect a name hint.
            srctex.file_name = TextureWriter.export_texture_filename(n_texture)
        else:
            # this probably should not happen
            NifLog.warn("Exporting source texture without texture or filename (bug?).")
            
        # (name normalization deferred until after embed/external handling)
        # Set FormatPrefs - these are nested inside format_prefs struct
        # Use LAY_DEFAULT (6) for modern NIF versions (>= 10.0.1.0)
        # Fallback to LAY_PALETTIZED_4 (5) for legacy versions
        # else if the scene game is ZONE4 also use pixel layout 6
        if bpy.context.scene.niftools_scene.nif_version >= 0x0A000100:
            srctex.format_prefs.pixel_layout = 6
        elif bpy.context.scene.niftools_scene.game == 'ZONE4':
            srctex.format_prefs.pixel_layout = 6
        else:
            srctex.format_prefs.pixel_layout = 5
        srctex.format_prefs.use_mipmaps = 1
        srctex.format_prefs.alpha_format = 3
        srctex.is_static = 1

        source_path = None
        target_fourcc = None
        # if embedding requested, delegate to compact helpers that wrap texconv and DDS parsing
        if embed:
            try:
                # Resolve any source texture path (png, tga, jpg, dds, etc.)
                tex_path = TextureWriter._resolve_texture_path(n_texture, filename)
                if not tex_path:
                    NifLog.warn("Embed Textures enabled but no readable DDS path was resolved; falling back to external reference.")
                    srctex.use_external = True
                else:
                    source_path = bpy.path.abspath(tex_path)
                    # Determine format based on node property or alpha usage
                    target_fourcc = 'AUTO'

                    # Check if the node itself has a specific format set
                    if isinstance(n_texture, bpy.types.ShaderNodeTexImage) and hasattr(n_texture, "niftools"):
                        pixel_format = n_texture.niftools.pixel_format
                        mapping = {
                            'FMT_RGB': 'R8G8B8A8_UNORM',
                            'FMT_RGBA': 'R8G8B8A8_UNORM',
                            'FMT_PAL': 'R8_UNORM',
                            'FMT_PALA': 'R8G8_UNORM',
                            'FMT_DXT1': 'DXT1',
                            'FMT_DXT3': 'DXT3',
                            'FMT_DXT5': 'DXT5',
                            'FMT_RGB24NONINT': 'R8G8B8A8_UNORM',
                            'FMT_BUMP': 'BC5_UNORM',
                            'FMT_BUMPLUMA': 'BC5_UNORM',
                            'FMT_RENDERSPEC': 'BC7_UNORM',
                            'FMT_1CH': 'R8_UNORM',
                            'FMT_2CH': 'R8G8_UNORM',
                            'FMT_3CH': 'R8G8B8A8_UNORM',
                            'FMT_4CH': 'R8G8B8A8_UNORM',
                            'FMT_DEPTH_STENCIL': 'AUTO',
                            'FMT_UNKNOWN': 'AUTO',
                            'AUTO': 'AUTO'
                        }
                        target_fourcc = mapping.get(pixel_format, 'AUTO')

                    if target_fourcc == 'AUTO':
                        if TextureWriter._is_transparency_used(n_texture):
                            target_fourcc = 'DXT5'
                            NifLog.info(f"Transparency detected in '{getattr(n_texture, 'name', 'texture')}', using DXT5.")
                        else:
                            target_fourcc = 'DXT1'
                            NifLog.info(f"No transparency detected in '{getattr(n_texture, 'name', 'texture')}', using DXT1.")
                    else:
                        NifLog.info(f"Using override format '{target_fourcc}' for '{getattr(n_texture, 'name', 'texture')}'.")
            except Exception as ex:
                NifLog.warn(f"Failed to resolve texture path for embedding: {ex}. Falling back to external reference.")
                srctex.use_external = True

        # Simple lookup: compute a key and reuse an existing block if present.
        source_key = TextureWriter._build_source_texture_key(srctex, source_path=source_path, target_fourcc=target_fourcc)
        existing = TextureWriter._source_texture_key_map.get(source_key)
        if existing:
            if not getattr(existing, 'file_name', None) and getattr(srctex, 'file_name', None):
                existing.file_name = srctex.file_name
                NifLog.debug(f"Updated duplicate block's file_name to: '{existing.file_name}'")
            if hasattr(existing, 'name'):
                if not getattr(existing, 'name', None) and getattr(srctex, 'name', None):
                    existing.name = srctex.name
                    NifLog.debug(f"Updated duplicate block's name to: '{existing.name}'")
                elif not getattr(existing, 'name', None) and getattr(existing, 'file_name', None):
                    existing.name = os.path.basename(existing.file_name)
                    NifLog.debug(f"Set duplicate block's name from filename: '{existing.name}'")
            block_store.invalidate_hash(existing)
            return existing

        if embed and not srctex.use_external and source_path and target_fourcc:
            try:
                # Convert to appropriate DXT format before embedding
                data, header = TextureWriter._convert_with_texconv_cached(source_path, desired_fourcc=target_fourcc)
                pix = TextureWriter._build_nipixeldata_from_dds(data, header)

                # Preserve filename/name; then attach pixel data
                original_filename = getattr(srctex, 'file_name', '')
                original_name = getattr(srctex, 'name', '')
                try:
                    srctex.pixel_data = pix
                except Exception:
                    srctex.data = pix

                if original_filename and not getattr(srctex, 'file_name', None):
                    srctex.file_name = original_filename
                if hasattr(srctex, 'name'):
                    if original_name:
                        srctex.name = original_name
                    elif getattr(srctex, 'file_name', None):
                        srctex.name = os.path.basename(srctex.file_name)
                    else:
                        srctex.name = os.path.basename(source_path)
            except Exception as ex:
                NifLog.warn(f"Failed to embed texture as NiPixelData: {ex}. Falling back to external reference.")
                srctex.use_external = True
                # Rebuild key for external export and reuse if already created.
                source_key = TextureWriter._build_source_texture_key(srctex)
                existing = TextureWriter._source_texture_key_map.get(source_key)
                if existing:
                    block_store.invalidate_hash(existing)
                    return existing
        elif embed and not srctex.use_external:
            NifLog.warn("Embed Textures enabled but no valid source texture resolved; exporting as external reference.")

        # Normalize name once, after embed/external decisions
        if hasattr(srctex, 'name') and not getattr(srctex, 'name', None):
            if n_texture is not None and isinstance(n_texture, bpy.types.ShaderNodeTexImage) and getattr(n_texture, 'image', None):
                srctex.name = n_texture.image.name
            elif getattr(srctex, 'file_name', None):
                srctex.name = os.path.basename(srctex.file_name)

        # Ensure file_name is present (User Request) even if embedding
        if not getattr(srctex, 'file_name', None):
            if getattr(srctex, 'name', None):
                srctex.file_name = srctex.name
            elif n_texture:
                 try:
                     srctex.file_name = TextureWriter.export_texture_filename(n_texture)
                 except Exception:
                     pass

        # Log the texture name for debugging
        NifLog.info(f"Exporting texture with name: '{getattr(srctex, 'file_name', '<none>')}', external: {srctex.use_external}")
        NifLog.info(f"NiSourceTexture.name = '{getattr(srctex, 'name', '<none>')}'")
        
        # no identical source texture found, so use and register the new one
        block = block_store.register_block(srctex, n_texture)
        TextureWriter._register_source_texture_key(block, source_key)
        return block

    def export_tex_desc(self, texdesc=None, uv_set=0, b_texture_node=None):
        """Helper function for export_texturing_property to export each texture slot."""
        texdesc.uv_set = uv_set
        texdesc.source = TextureWriter.export_source_texture(b_texture_node)

    @staticmethod
    def export_texture_filename(b_texture_node):
        """Returns image file name from b_texture_node.

        @param b_texture_node: The b_texture_node object in blender.
        @return: The file name of the image used in the b_texture_node.
        """

        if not isinstance(b_texture_node, bpy.types.ShaderNodeTexImage):
            raise io_scene_niftools.utils.logging.NifError(f"Expected a Shader node texture, got {type(b_texture_node)}")
        # get filename from image

        # TODO [b_texture_node] still needed? can b_texture_node.image be None in current blender?
        # check that image is loaded
        if b_texture_node.image is None:
            raise io_scene_niftools.utils.logging.NifError(f"Image type texture has no file loaded ('{b_texture_node.name}')")

        filename = b_texture_node.image.filepath
        if not filename:
             filename = b_texture_node.image.name

        # warn if packed flag is enabled
        if b_texture_node.image.packed_file:
            NifLog.warn(f"Packed image in texture '{b_texture_node.name}' ignored, exporting as '{filename}' instead.")

        # try and find a DDS alternative, force it if required
        ddsfilename = f"{(filename[:-4])}.dds"
        if os.path.exists(bpy.path.abspath(ddsfilename)) or NifOp.props.force_dds:
            filename = ddsfilename

        # sanitize file path
        nif_scene = bpy.context.scene.niftools_scene
        if not (nif_scene.is_bs() or nif_scene.game in ('MORROWIND',)):
            # strip b_texture_node file path
            filename = os.path.basename(filename)

        else:
            # strip the data files prefix from the b_texture_node's file name
            filename = filename.lower()
            idx = filename.find("textures")
            if idx >= 0:
                filename = filename[idx:]
            elif not os.path.exists(bpy.path.abspath(filename)):
                pass
            else:
                NifLog.warn(f"{filename} does not reside in a 'Textures' folder; texture path will be stripped and textures may not display in-game")
                filename = os.path.basename(filename)
        # for linux export: fix path separators
        return filename.replace('/', '\\')

    # ------------------------------
    # Helpers for compact embed flow
    # ------------------------------
    @staticmethod
    def _is_transparency_used(n_texture):
        """Detect if the alpha channel of the texture is used for transparency.
        
        Checks the Alpha setting on the Image Texture node:
        - If Alpha is set to 'NONE', returns False (use DXT1)
        - If Alpha is set to anything else ('STRAIGHT', 'PREMUL', 'CHANNEL_PACKED'), returns True (use DXT5)
        
        Returns True if transparency is detected, False otherwise.
        """
        if not n_texture or not isinstance(n_texture, bpy.types.ShaderNodeTexImage):
            return False

        # Check the image's alpha_mode setting
        if n_texture.image is None:
            return False
        
        # alpha_mode can be: 'NONE', 'STRAIGHT', 'PREMUL', 'CHANNEL_PACKED'
        alpha_mode = getattr(n_texture.image, 'alpha_mode', 'NONE')
        
        if alpha_mode == 'NONE':
            NifLog.debug(f"Texture '{n_texture.name}' has Alpha set to NONE, using DXT1.")
            return False
        else:
            NifLog.debug(f"Texture '{n_texture.name}' has Alpha set to '{alpha_mode}', using DXT5.")
            return True

    @staticmethod
    def _resolve_texture_path(n_texture, filename):
        """Resolve a usable source texture path (any format). Prefers DDS but will return
        the first existing candidate among common image formats or any path that exists."""
        candidates = []
        if filename:
            candidates.append(filename)
        if n_texture is not None and isinstance(n_texture, bpy.types.ShaderNodeTexImage) and n_texture.image:
            img = n_texture.image
            for p in (getattr(img, 'filepath', None), getattr(img, 'filepath_raw', None)):
                if p:
                    candidates.append(p)
                    # add corresponding .dds variant as a preference
                    # root, ext = os.path.splitext(p)
                    # if ext:
                    #     candidates.append(root + '.dds')
            try:
                candidates.append(TextureWriter.export_texture_filename(n_texture))
            except Exception:
                pass
        # resolve relative to blend folder if needed
        try:
            blend_dir = bpy.path.abspath('//')
            for c in list(candidates):
                if c and (not os.path.isabs(c)):
                    candidates.append(os.path.join(blend_dir, c))
        except Exception:
            pass
        # de-duplicate
        seen = set()
        uniq = []
        for c in candidates:
            if c and c not in seen:
                uniq.append(c)
                seen.add(c)
        for c in uniq:
            # remove blender numeric suffix if present (e.g. .001)
            import re
            c_base = os.path.basename(c)
            # Match pattern: name + optional extension + .001, .002 etc
            # We want to strip the .XXX suffix but keep the extension
            if re.search(r'\.\d{3}$', c):
                # Check if it has an extension before the suffix
                root, suffix = os.path.splitext(c)
                if suffix and re.match(r'^\.\d{3}$', suffix):
                   candidates.append(root)

            NifLog.debug(f"Embed resolve candidate: '{c}' -> '{bpy.path.abspath(c)}'")
        # 1) Prefer existing DDS
        for c in uniq:
            absc = bpy.path.abspath(c)
            if absc.lower().endswith('.dds') and os.path.exists(absc):
                return absc
        # 2) Otherwise return first existing file among common formats or any existing path
        for c in uniq:
            absc = bpy.path.abspath(c)
            if os.path.exists(absc):
                return absc
        return None

    @staticmethod
    def _read_file_bytes(path):
        with open(path, 'rb') as f:
            return f.read()

    @staticmethod
    def _parse_dds_header(data):
        if data[:4] != b'DDS ':
            raise ValueError('Not a DDS file')
        header = data[4:128]
        dwSize, dwFlags, dwHeight, dwWidth, dwPitchOrLinearSize, dwDepth, dwMipMapCount = struct.unpack('<7I', header[0:28])
        pf = header[76:108]
        (pfSize, pfFlags, pfFourCC, pfRGBBitCount, pfRMask, pfGMask, pfBMask, pfAMask) = struct.unpack('<II4sI4I', pf)
        fourcc = pfFourCC.decode('ascii', errors='ignore').strip('\x00').strip()
        payload_offset = 128
        if fourcc == 'DX10':
            if len(data) < 148:
                raise ValueError('DX10 header truncated')
            (dxgi_format, resource_dim, misc_flag, array_size, misc_flags2) = struct.unpack('<5I', data[128:148])
            payload_offset = 148
            if dxgi_format in (71, 72):
                fourcc = 'DXT1'
            elif dxgi_format in (74, 75):
                fourcc = 'DXT3'
            elif dxgi_format in (77, 78):
                fourcc = 'DXT5'
            elif dxgi_format == 80:
                fourcc = 'BC4_UNORM'
            elif dxgi_format == 83:
                fourcc = 'BC5_UNORM'
            elif dxgi_format == 95:
                fourcc = 'BC6H_UF16'
            elif dxgi_format == 98:
                fourcc = 'BC7_UNORM'
            elif dxgi_format == 61:
                fourcc = 'R8_UNORM'
            elif dxgi_format == 49:
                fourcc = 'R8G8_UNORM'
            elif dxgi_format == 28:
                fourcc = 'R8G8B8A8_UNORM'
            elif dxgi_format == 10:
                fourcc = 'R16G16B16A16_FLOAT'
            elif dxgi_format == 2:
                fourcc = 'R32G32B32A32_FLOAT'
            else:
                raise ValueError(f'Unsupported DXGI format {dxgi_format}')
        if not fourcc:
            NifLog.info("Empty fourcc detected, returning None")
            fourcc = None
        return {
            'width': dwWidth,
            'height': dwHeight,
            'flags': dwFlags,
            'mip_count': dwMipMapCount if (dwFlags & 0x20000) and dwMipMapCount > 0 else 1,
            'fourcc': fourcc,
            'payload_offset': payload_offset,
            'pitch': dwPitchOrLinearSize,
            'bit_count': pfRGBBitCount,
        }

    @staticmethod
    def _convert_with_texconv(src_path, desired_fourcc='DXT1'):
        import tempfile, subprocess
        src = bpy.path.abspath(src_path)
        # Resolve texconv path
        user_texconv = getattr(NifOp.props, 'texconv_path', '')
        if user_texconv:
            texconv = user_texconv
        else:
            try:
                import io_scene_niftools as _nif_addon_pkg
                addon_dir = os.path.dirname(os.path.abspath(_nif_addon_pkg.__file__))
            except Exception:
                addon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            bundled_new = os.path.join(addon_dir, 'dependencies', 'bin', 'texconv.exe')
            NifLog.info(f"texconv resolution: addon_dir='{addon_dir}'")
            NifLog.info(f"texconv candidate (new)='{bundled_new}' exists={os.path.exists(bundled_new)}")
            if os.path.exists(bundled_new):
                texconv = bundled_new
                NifLog.info(f"Using bundled texconv at '{texconv}'")
            else:
                texconv = 'texconv'

        with tempfile.TemporaryDirectory(prefix='nif_embed_') as workdir:
            cmd = [texconv, '-nologo', '-f', desired_fourcc, '-o', workdir, src]
            NifLog.info(f"Converting '{src}' to {desired_fourcc} via: {' '.join(cmd)}")
            subprocess.run(cmd, check=True, capture_output=True)
            # Find produced dds
            produced = None
            base = os.path.splitext(os.path.basename(src))[0]
            for cand in os.listdir(workdir):
                if cand.lower().startswith(base.lower()) and cand.lower().endswith('.dds'):
                    produced = os.path.join(workdir, cand)
                    break
            if not produced:
                raise RuntimeError('texconv did not produce a DDS file')
            data = TextureWriter._read_file_bytes(produced)
        header = TextureWriter._parse_dds_header(data)
        # Some texconv builds may leave pfFourCC empty; trust requested format
        if not header['fourcc']:
            header['fourcc'] = desired_fourcc
        
        # If header has fourcc but it differs from desired, we trust the header unless it was empty
        # but warn if mismatch? Actually texconv should have produced the desired format.
        
        # Validate format is supported by writer
        supported_formats = (
            'DXT1', 'DXT3', 'DXT5', 
            'BC1_UNORM', 'BC2_UNORM', 'BC3_UNORM', 'BC4_UNORM', 'BC5_UNORM', 'BC6H_UF16', 'BC7_UNORM',
            'R8_UNORM', 'R8G8_UNORM', 'R8G8B8A8_UNORM', 'R16G16B16A16_FLOAT', 'R32G32B32A32_FLOAT'
        )
        if not header['fourcc'] or header['fourcc'] not in supported_formats:
            raise ValueError(f"Auto-convert produced unsupported format '{header['fourcc']}'")
        NifLog.info(f"Convert succeeded. New DDS fourcc='{header['fourcc']}', size={header['width']}x{header['height']}")
        return data, header

    @staticmethod
    def _convert_with_texconv_cached(src_path, desired_fourcc='DXT1'):
        src = bpy.path.abspath(src_path)
        cache_key = (src, desired_fourcc)
        cached = TextureWriter._embed_cache.get(cache_key)
        if cached:
            NifLog.info(f"Reusing cached texconv output for '{src}' ({desired_fourcc})")
            data, header = cached
            return data, dict(header)
        data, header = TextureWriter._convert_with_texconv(src, desired_fourcc=desired_fourcc)
        TextureWriter._embed_cache[cache_key] = (data, dict(header))
        return data, header

    @staticmethod
    def _build_nipixeldata_from_dds(data, header):
        fourcc_to_fmt = {
            'DXT1': ('FMT_DXT1', 8, True),
            'DXT3': ('FMT_DXT3', 16, True),
            'DXT5': ('FMT_DXT5', 16, True),
            'BC1_UNORM': ('FMT_DXT1', 8, True),
            'BC2_UNORM': ('FMT_DXT3', 16, True),
            'BC3_UNORM': ('FMT_DXT5', 16, True),
            'BC4_UNORM': ('FMT_RENDERSPEC', 8, True),
            'BC5_UNORM': ('FMT_RENDERSPEC', 16, True),
            'BC6H_UF16': ('FMT_RENDERSPEC', 16, True),
            'BC7_UNORM': ('FMT_RENDERSPEC', 16, True),
            'R8_UNORM': ('FMT_1CH', 1, False),
            'R8G8_UNORM': ('FMT_2CH', 2, False),
            'RGB24': ('FMT_RGB', 3, False),
            'R8G8B8A8_UNORM': ('FMT_RGBA', 4, False),
            'R16G16B16A16_FLOAT': ('FMT_4CH', 8, False),
            'R32G32B32A32_FLOAT': ('FMT_4CH', 16, False),
        }
        fourcc = header['fourcc']
        if not fourcc and header.get('bit_count') == 24:
            fourcc = 'RGB24'
        if fourcc not in fourcc_to_fmt:
            raise ValueError(f"Unsupported DDS format '{fourcc}' for embedding")
        payload = data[header['payload_offset']:]
        w, h = header['width'], header['height']
        mip_total = header['mip_count']
        
        # Determine how many mips to write based on operator property
        embed_only_base = getattr(NifOp.props, 'embed_only_base_mipmap', True)
        mip_to_write = 1 if embed_only_base else mip_total
        
        fmt_info = fourcc_to_fmt[fourcc]
        bytes_per_unit = fmt_info[1]
        is_compressed = fmt_info[2]

        offset = 0
        chunks = []
        mip_entries = []
        header_pitch = header.get('pitch', 0)
        for i in range(mip_to_write):
            if is_compressed:
                bw = max(1, (w + 3) // 4)
                bh = max(1, (h + 3) // 4)
                size = bw * bh * bytes_per_unit
            else:
                # compute pitch (bytes per scanline) aligned to 4 bytes
                pitch = ((w * bytes_per_unit + 3) // 4) * 4
                if header_pitch and i == 0:
                    # validate that computed pitch matches header pitch
                    if pitch != header_pitch:
                        NifLog.warn(f"Pitch mismatch: computed {pitch}, header {header_pitch}. Using computed.")
                size = pitch * h
            chunks.append(payload[offset:offset + size])
            mip_entries.append((w, h, offset, size))
            offset += size
            w = max(1, w // 2)
            h = max(1, h // 2)

        pix = NifClasses.NiPixelData(NifData.data)
        # pixel_format
        fmt_name = fmt_info[0]
        try:
            pix.pixel_format = getattr(NifClasses.PixelFormat, fmt_name)
        except Exception:
            try:
                pix.pixel_format = fmt_name
            except Exception:
                pass
        # tiling
        try:
            pix.tiling = NifClasses.PixelTiling.TILE_NONE
        except Exception:
            try:
                pix.tiling = 'TILE_NONE'
            except Exception:
                pass
        # additional fields for uncompressed formats
        bytes_per_pixel = 0 if is_compressed else bytes_per_unit
        try:
            pix.bytes_per_pixel = bytes_per_pixel
            NifLog.debug(f"Set bytes_per_pixel = {bytes_per_pixel}")
        except Exception:
            NifLog.debug("Could not set bytes_per_pixel")
        # bits per pixel (0 for compressed, else bits per pixel)
        bits_per_pixel = 0 if is_compressed else bytes_per_unit * 8
        try:
            pix.bits_per_pixel = bits_per_pixel
            NifLog.debug(f"Set bits_per_pixel = {bits_per_pixel}")
        except Exception:
            NifLog.debug("Could not set bits_per_pixel")

        # set masks and channels for uncompressed RGB(A) formats
        if not is_compressed:
            # Mask mapping for uncompressed formats (older versions)
            if hasattr(NifData, 'data') and hasattr(NifData.data, 'version') and NifData.data.version <= 168034305:
                if fourcc == 'R8_UNORM':
                    pix.red_mask = 0xFF
                    pix.green_mask = 0
                    pix.blue_mask = 0
                    pix.alpha_mask = 0
                    NifLog.debug("Set R8 masks")
                elif fourcc == 'R8G8_UNORM':
                    pix.red_mask = 0x00FF
                    pix.green_mask = 0xFF00
                    pix.blue_mask = 0
                    pix.alpha_mask = 0
                    NifLog.debug("Set R8G8 masks")
                elif fourcc == 'RGB24':
                    pix.red_mask = 0x000000FF
                    pix.green_mask = 0x0000FF00
                    pix.blue_mask = 0x00FF0000
                    pix.alpha_mask = 0
                    NifLog.debug("Set RGB24 masks")
                elif fourcc == 'R8G8B8A8_UNORM':
                    pix.red_mask = 0x000000FF
                    pix.green_mask = 0x0000FF00
                    pix.blue_mask = 0x00FF0000
                    pix.alpha_mask = 0xFF000000
                    NifLog.debug("Set RGBA masks")

            # Channel mapping for uncompressed formats (newer versions or versions with channels array)
            if hasattr(pix, "channels"):
                channel_configs = {
                    'R8_UNORM': [('COMP_RED', 8)],
                    'R8G8_UNORM': [('COMP_RED', 8), ('COMP_GREEN', 8)],
                    'RGB24': [('COMP_RED', 8), ('COMP_GREEN', 8), ('COMP_BLUE', 8)],
                    'R8G8B8A8_UNORM': [('COMP_RED', 8), ('COMP_GREEN', 8), ('COMP_BLUE', 8), ('COMP_ALPHA', 8)]
                }
                config = channel_configs.get(fourcc)
                if config:
                    for i, (ctype, bits) in enumerate(config):
                        if i < len(pix.channels):
                            c = pix.channels[i]
                            try:
                                c.type = getattr(NifClasses.PixelComponent, ctype)
                            except AttributeError:
                                c.type = ctype
                            try:
                                c.convention = getattr(NifClasses.PixelRepresentation, 'REP_NORM_INT')
                            except AttributeError:
                                c.convention = 'REP_NORM_INT'
                            c.bits_per_channel = bits
                            c.is_signed = False
                    NifLog.debug(f"Set channel bits for {fourcc}")

            # Old Fast Compare for older versions (<= 10.3.0.2)
            if hasattr(pix, "old_fast_compare") and hasattr(NifData, 'data') and hasattr(NifData.data, 'version') and NifData.data.version <= 168034305:
                if fourcc == 'R8_UNORM':
                    # 8bpp
                    pix.old_fast_compare[:] = [34, 0, 0, 0, 0, 0, 0, 0]
                    NifLog.debug("Set R8 old_fast_compare")
                elif fourcc == 'R8G8B8A8_UNORM':
                    # 32bpp (signed byte representation: 129 -> -127, 130 -> -126)
                    pix.old_fast_compare[:] = [-127, 8, -126, 32, 0, 8, 16, 24]
                    NifLog.debug("Set RGBA old_fast_compare")

        # mip maps meta
        try:
            pix.num_mipmaps = len(mip_entries)
        except Exception:
            pass
        try:
            pix.reset_field('mipmaps')
            for n, (mw, mh, mo, npix) in enumerate(mip_entries):
                m = pix.mipmaps[n]
                m.width = mw
                m.height = mh
                m.offset = mo
                m.num_pixels = npix
        except Exception:
            pass

        concatenated = b''.join(chunks)
        arr = np.frombuffer(concatenated, dtype=np.uint8)
        try:
            pix.num_pixels = len(arr)
        except Exception:
            NifLog.warn("Could not set num_pixels on NiPixelData")

        try:
            if hasattr(pix, 'num_faces') and getattr(pix, 'num_faces', 1) > 1:
                expected_len = pix.num_pixels * pix.num_faces
            else:
                expected_len = pix.num_pixels
            if len(arr) != expected_len:
                NifLog.warn(f"Pixel data length mismatch: got {len(arr)}, expected {expected_len}")
                if hasattr(pix, 'num_faces') and getattr(pix, 'num_faces', 1) > 1:
                    pix.num_pixels = len(arr) // max(1, pix.num_faces)
                else:
                    pix.num_pixels = len(arr)
            # shape according to version
            if hasattr(NifData, 'data') and hasattr(NifData.data, 'version'):
                if NifData.data.version >= 168034306 and hasattr(pix, 'num_faces') and getattr(pix, 'num_faces', 1) > 1:
                    arr = arr.reshape((pix.num_pixels * pix.num_faces,))
                else:
                    arr = arr.reshape((pix.num_pixels,))
            else:
                arr = arr.reshape((pix.num_pixels,))
            try:
                pix.pixel_data = arr
            except Exception as e:
                NifLog.warn(f"Failed to assign pixel_data: {e}")
                pix.data = arr
        except Exception as outer_ex:
            NifLog.error(f"Error processing pixel data: {outer_ex}")
            raise

        return pix

