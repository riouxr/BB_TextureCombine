bl_info = {
    "name": "BB Texture Combine",
    "author": "Blender Bob & Claude.ai",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "Shader Editor > N Panel > BB Texture Combine",
    "description": "Combine multiple textures with UDIM support",
    "category": "Material",
}

import bpy
import bmesh
import os
import sys
import math
from mathutils import Vector
from collections import defaultdict


def get_udim_tile_from_uv(u, v):
    """
    Calculate UDIM tile number from UV coordinates.
    Handles boundary case: UV exactly at integer boundary (1.0, 2.0, etc) 
    belongs to the tile BEFORE it, not after.
    """
    # Adjust boundary UVs to stay in current tile
    u_adjusted = u if u != int(u) or u == 0 else u - 0.001
    v_adjusted = v if v != int(v) or v == 0 else v - 0.001
    
    tile_u = math.floor(u_adjusted)
    tile_v = math.floor(v_adjusted)
    
    return 1001 + (tile_v * 10) + tile_u


def get_object_primary_udim(obj):
    """
    Determine which UDIM tile an object primarily belongs to based on 
    the center point of its UV bounding box. Much faster than counting all UVs.
    """
    if obj.type != 'MESH' or not obj.data.uv_layers:
        return None
    
    uv_layer = obj.data.uv_layers.active
    if not uv_layer:
        return None
    
    # Get UV bounding box
    if not obj.data.loops:
        return None
    
    first_uv = uv_layer.data[obj.data.loops[0].index].uv
    min_u = max_u = first_uv.x
    min_v = max_v = first_uv.y
    
    for loop in obj.data.loops:
        uv = uv_layer.data[loop.index].uv
        min_u = min(min_u, uv.x)
        max_u = max(max_u, uv.x)
        min_v = min(min_v, uv.y)
        max_v = max(max_v, uv.y)
    
    # Calculate center point
    center_u = (min_u + max_u) / 2.0
    center_v = (min_v + max_v) / 2.0
    
    # Determine tile from center point
    tile_u = math.floor(center_u)
    tile_v = math.floor(center_v)
    
    return 1001 + (tile_v * 10) + tile_u


class BBTextureCombineProperties(bpy.types.PropertyGroup):
    target_udim_count: bpy.props.IntProperty(
        name="Target UDIMs",
        description="Number of UDIM tiles to pack textures into",
        default=1,
        min=1,
        max=100
    )
    
    combine_objects: bpy.props.BoolProperty(
        name="Combine Objects",
        description="Create a single material for all selected objects",
        default=False
    )
    
    use_lossless: bpy.props.BoolProperty(
        name="Lossless Mode",
        description="Automatically calculate resolution to preserve source texture quality",
        default=False
    )
    
    output_resolution: bpy.props.IntProperty(
        name="Texture Resolution",
        description="Resolution per UDIM tile (ignored in Lossless mode)",
        default=4096,
        min=512,
        max=8192
    )


class BBTextureCombinePanel(bpy.types.Panel):
    bl_label = "BB Texture Combine"
    bl_idname = "SHADER_PT_bb_texture_combine"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "BB Texture Combine"
    bl_context = "shader"

    def draw(self, context):
        layout = self.layout
        props = context.scene.bb_texture_combine_props
        
        layout.label(text="Texture Combining", icon='TEXTURE')
        
        box = layout.box()
        box.label(text=f"Selected Objects: {len(context.selected_objects)}")
        
        if len(context.selected_objects) > 0:
            current_udims = get_current_udim_count(context.selected_objects)
            box.label(text=f"Current UDIMs: {current_udims}")
        
        layout.separator()
        
        layout.prop(props, "target_udim_count")
        
        row = layout.row()
        row.prop(props, "use_lossless")
        
        if not props.use_lossless:
            layout.prop(props, "output_resolution")
        else:
            box = layout.box()
            box.label(text="Resolution will be auto-calculated", icon='INFO')
        
        layout.prop(props, "combine_objects")
        
        layout.separator()
        
        row = layout.row()
        row.scale_y = 2.0
        row.operator("object.bb_combine_textures", text="Combine Textures", icon='RENDER_RESULT')


def get_current_udim_count(objects):
    """Detect how many UDIM tiles are currently used"""
    udim_tiles = set()
    
    for obj in objects:
        if obj.type != 'MESH':
            continue
            
        if not obj.data.uv_layers:
            continue
            
        uv_layer = obj.data.uv_layers.active
        if not uv_layer:
            continue
        
        for loop in obj.data.loops:
            uv = uv_layer.data[loop.index].uv
            udim = get_udim_tile_from_uv(uv.x, uv.y)
            udim_tiles.add(udim)
    
    return len(udim_tiles) if udim_tiles else 0


def get_all_texture_nodes(material):
    """Find ALL texture nodes in a material, regardless of where they're connected"""
    textures = {}
    
    if not material or not material.use_nodes:
        return textures
    
    # Find all TEX_IMAGE nodes
    for node in material.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            # Use the image name directly to identify each texture uniquely
            # This ensures every texture is detected, not just the first one that connects to a socket
            socket_name = node.image.name.split('.')[0].replace('_', ' ').title()
            
            textures[socket_name] = {
                'image': node.image,
                'node': node
            }
            print(f"    Found texture: {socket_name} = {node.image.name}")
    
    return textures


def determine_texture_usage(material, texture_node):
    """Try to determine what a texture node is used for by following its connections"""
    
    # Check if any output is connected
    if not texture_node.outputs['Color'].is_linked:
        return None
    
    # Follow connections to see where they end up
    visited = set()
    
    def trace_to_principled(node, socket_name=None):
        if node in visited:
            return None
        visited.add(node)
        
        # If we reached Principled BSDF, return which input we're connected to
        if node.type == 'BSDF_PRINCIPLED':
            # Find which input is connected from our path
            for input_name, input_socket in node.inputs.items():
                if input_socket.is_linked:
                    for link in input_socket.links:
                        if link.from_node == texture_node or link.from_node in visited:
                            return input_name
            return None
        
        # If we reached a normal map node, it's for Normal
        if node.type == 'NORMAL_MAP':
            return "Normal"
        
        # Follow outputs to next nodes
        for output in node.outputs:
            if output.is_linked:
                for link in output.links:
                    result = trace_to_principled(link.to_node)
                    if result:
                        return result
        
        return None
    
    # Start tracing from the texture node
    for output in texture_node.outputs:
        if output.is_linked:
            for link in output.links:
                usage = trace_to_principled(link.to_node)
                if usage:
                    return usage
    
    return None


def get_principled_bsdf_textures(material):
    """Extract image texture nodes connected to Principled BSDF"""
    if not material.use_nodes:
        return {}
    
    textures = {}
    nodes = material.node_tree.nodes
    
    # Find Principled BSDF
    principled = None
    for node in nodes:
        if node.type == 'BSDF_PRINCIPLED':
            principled = node
            break
    
    if not principled:
        return {}
    
    # Check each input socket
    for input_socket in principled.inputs:
        if input_socket.is_linked:
            linked_node = input_socket.links[0].from_node
            
            # Handle normal map case
            if input_socket.name == "Normal" and linked_node.type == 'NORMAL_MAP':
                if linked_node.inputs['Color'].is_linked:
                    linked_node = linked_node.inputs['Color'].links[0].from_node
            
            # Check if it's an image texture
            if linked_node.type == 'TEX_IMAGE' and linked_node.image:
                socket_name = input_socket.name
                textures[socket_name] = {
                    'image': linked_node.image,
                    'node': linked_node
                }
    
    return textures


def get_uv_bounds(obj):
    """Get UV bounds and area for an object"""
    if not obj.data.uv_layers:
        return None
    
    uv_layer = obj.data.uv_layers.active
    if not uv_layer:
        return None
    
    min_u, min_v = float('inf'), float('inf')
    max_u, max_v = float('-inf'), float('-inf')
    
    for loop in obj.data.loops:
        uv = uv_layer.data[loop.index].uv
        min_u = min(min_u, uv.x)
        min_v = min(min_v, uv.y)
        max_u = max(max_u, uv.x)
        max_v = max(max_v, uv.y)
    
    width = max_u - min_u
    height = max_v - min_v
    
    return {
        'min_u': min_u,
        'min_v': min_v,
        'max_u': max_u,
        'max_v': max_v,
        'width': width,
        'height': height,
        'area': width * height
    }


def get_object_texture_resolution(obj):
    """Get the resolution of the texture used by this object's material"""
    if not obj.material_slots:
        return 1024, 1024  # Default
    
    for mat_slot in obj.material_slots:
        if not mat_slot.material or not mat_slot.material.use_nodes:
            continue
        
        # Find Base Color texture (or any texture)
        for node in mat_slot.material.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                width, height = node.image.size[0], node.image.size[1]
                if width > 0 and height > 0:
                    return width, height
    
    return 1024, 1024  # Default if no texture found


def analyze_materials(objects):
    """Analyze all materials and collect ALL texture information"""
    texture_map = defaultdict(list)  # socket_name -> list of (object, material, image)
    
    print("\n=== ANALYZING MATERIALS ===")
    
    for obj in objects:
        if obj.type != 'MESH':
            continue
        
        for mat_slot in obj.material_slots:
            if not mat_slot.material:
                continue
            
            print(f"\nMaterial: {mat_slot.material.name} (on {obj.name})")
            
            # Method 1: Get textures from Principled BSDF inputs (standard PBR workflow)
            print("  Checking Principled BSDF inputs...")
            pbsdf_textures = get_principled_bsdf_textures(mat_slot.material)
            for name in pbsdf_textures.keys():
                print(f"    → {name}: {pbsdf_textures[name]['image'].name}")
            
            # Method 2: Get ALL texture nodes in material (catches Mix nodes, AO, etc.)
            print("  Checking all texture nodes...")
            all_textures = get_all_texture_nodes(mat_slot.material)
            
            # Merge strategy: use all_textures (image names) as primary
            # This ensures ALL textures are detected and converted
            merged_textures = {**all_textures}
            
            # Only add BSDF textures if they're not already present (by checking the actual image object)
            for socket_name, tex_info in pbsdf_textures.items():
                # Check if this image is already in merged_textures
                image_already_present = any(
                    info['image'] == tex_info['image'] 
                    for info in merged_textures.values()
                )
                
                if not image_already_present:
                    # This texture isn't in our list yet, add it with BSDF socket name
                    merged_textures[socket_name] = tex_info
            
            print(f"  Total textures found: {len(merged_textures)}")
            
            for socket_name, tex_info in merged_textures.items():
                texture_map[socket_name].append({
                    'object': obj,
                    'material': mat_slot.material,
                    'image': tex_info['image'],
                    'node': tex_info['node']
                })
    
    print(f"\n=== TEXTURE MAP SUMMARY ===")
    print(f"Total texture types: {len(texture_map)}")
    for socket_name, entries in texture_map.items():
        print(f"  {socket_name}: {len(entries)} entries")
    print("=" * 40 + "\n")
    
    return texture_map


def find_connected_texture_recursive(socket, visited=None):
    """Recursively search for a texture node connected to a socket, traversing through intermediate nodes"""
    if visited is None:
        visited = set()
    
    if not socket.is_linked:
        return None
    
    from_node = socket.links[0].from_node
    
    # Avoid infinite loops
    if from_node in visited:
        return None
    visited.add(from_node)
    
    # If we found a texture, return it
    if from_node.type == 'TEX_IMAGE' and from_node.image:
        return from_node.image
    
    # Otherwise, search through this node's inputs
    for input_socket in from_node.inputs:
        if input_socket.is_linked:
            texture = find_connected_texture_recursive(input_socket, visited)
            if texture:
                return texture
    
    return None


def detect_source_udims_for_socket(objects, socket_name):
    """Detect which source UDIM tiles are used and map textures for a SPECIFIC socket type"""
    source_tiles = set()
    tile_to_objects = {}  # tile_number -> list of objects
    tile_to_texture = {}  # tile_number -> texture for THIS socket type
    
    print(f"=== DETECTING SOURCE UDIMS FOR {socket_name} ===")
    
    # Determine if this is a standard Principled BSDF socket or a custom texture name
    is_standard_socket = socket_name in [
        'Base Color', 'Metallic', 'Roughness', 'Normal', 'Specular', 
        'Emission', 'Emission Color', 'Alpha', 'Transmission', 'Subsurface Color'
    ]
    
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.uv_layers:
            continue
        
        uv_layer = obj.data.uv_layers.active
        if not uv_layer:
            continue
        
        # Determine which UDIM tile this object primarily belongs to
        primary_tile = get_object_primary_udim(obj)
        if not primary_tile:
            continue
        
        obj_tiles = {primary_tile}
        
        # Get this object's texture FOR THIS SPECIFIC SOCKET
        obj_texture = None
        if obj.material_slots and obj.material_slots[0].material:
            mat = obj.material_slots[0].material
            if mat.use_nodes:
                if is_standard_socket:
                    # Standard socket: look at Principled BSDF input
                    principled = None
                    for node in mat.node_tree.nodes:
                        if node.type == 'BSDF_PRINCIPLED':
                            principled = node
                            break
                    
                    if principled and socket_name in principled.inputs:
                        # Find connected texture for this socket (recursively through any intermediate nodes)
                        socket = principled.inputs[socket_name]
                        
                        # Handle normal map node specially
                        if socket_name == "Normal" and socket.is_linked:
                            from_node = socket.links[0].from_node
                            if from_node.type == 'NORMAL_MAP' and from_node.inputs['Color'].is_linked:
                                obj_texture = find_connected_texture_recursive(from_node.inputs['Color'])
                            else:
                                obj_texture = find_connected_texture_recursive(socket)
                        else:
                            obj_texture = find_connected_texture_recursive(socket)
                else:
                    # Custom texture name: find texture node with matching name/usage
                    for node in mat.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            # Try multiple matching strategies
                            match = False
                            
                            # Strategy 1: Exact match on normalized name
                            image_name_normalized = node.image.name.replace('.', '_')
                            if socket_name == image_name_normalized:
                                match = True
                            
                            # Strategy 2: Socket name in image name
                            elif socket_name.lower().replace(' ', '') in node.image.name.lower().replace(' ', '').replace('_', ''):
                                match = True
                            
                            # Strategy 3: Common name patterns
                            img_lower = node.image.name.lower()
                            socket_lower = socket_name.lower()
                            
                            if 'ambient' in socket_lower and 'ao' in img_lower:
                                match = True
                            elif 'base color' in socket_lower and ('basecolor' in img_lower or 'diffuse' in img_lower):
                                match = True
                            
                            if match:
                                obj_texture = node.image
                                break
        
        if obj_texture:
            print(f"Object '{obj.name}' in tiles {sorted(obj_tiles)}: {obj_texture.name}")
        
        # Map objects and textures to tiles
        for tile_num in obj_tiles:
            source_tiles.add(tile_num)
            
            if tile_num not in tile_to_objects:
                tile_to_objects[tile_num] = []
            tile_to_objects[tile_num].append(obj)
            
            # Store texture for this tile (first one found)
            if tile_num not in tile_to_texture and obj_texture:
                tile_to_texture[tile_num] = obj_texture
                print(f"  → MAPPED: UDIM {tile_num} = {obj_texture.name}")
    
    sorted_tiles = sorted(list(source_tiles))
    print(f"\n=== FINAL SOURCE UDIM MAPPING FOR {socket_name} ===")
    for tile_num in sorted_tiles:
        tex = tile_to_texture.get(tile_num)
        tex_name = tex.name if tex else "NONE"
        objs = [o.name for o in tile_to_objects.get(tile_num, [])]
        print(f"UDIM {tile_num}: {tex_name} | Objects: {objs}")
    print("=" * 40 + "\n")
    
    return sorted_tiles, tile_to_objects, tile_to_texture


def detect_source_udims(objects):
    """Detect which source UDIM tiles are used and map textures to them"""
    source_tiles = set()
    tile_to_objects = {}  # tile_number -> list of objects
    tile_to_texture = {}  # tile_number -> texture (for compositor)
    
    print("=== DETECTING SOURCE UDIMS ===")
    
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.uv_layers:
            continue
        
        uv_layer = obj.data.uv_layers.active
        if not uv_layer:
            continue
        
        # Determine which UDIM tile this object primarily belongs to
        primary_tile = get_object_primary_udim(obj)
        if not primary_tile:
            continue
        
        obj_tiles = {primary_tile}
        
        print(f"Object '{obj.name}' uses UDIM tiles: {sorted(obj_tiles)}")
        
        # Get this object's texture
        obj_texture = None
        if obj.material_slots and obj.material_slots[0].material:
            mat = obj.material_slots[0].material
            if mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        obj_texture = node.image
                        print(f"  → Texture: {obj_texture.name}")
                        break
        
        if not obj_texture:
            print(f"  → No texture found!")
        
        # Map objects and textures to tiles
        for tile_num in obj_tiles:
            source_tiles.add(tile_num)
            
            if tile_num not in tile_to_objects:
                tile_to_objects[tile_num] = []
            tile_to_objects[tile_num].append(obj)
            
            # Store texture for this tile (first one found)
            if tile_num not in tile_to_texture and obj_texture:
                tile_to_texture[tile_num] = obj_texture
                print(f"  → MAPPED: UDIM {tile_num} = {obj_texture.name}")
    
    sorted_tiles = sorted(list(source_tiles))
    print(f"\n=== FINAL SOURCE UDIM MAPPING ===")
    for tile_num in sorted_tiles:
        tex = tile_to_texture.get(tile_num)
        tex_name = tex.name if tex else "NONE"
        objs = [o.name for o in tile_to_objects.get(tile_num, [])]
        print(f"UDIM {tile_num}: {tex_name} | Objects: {objs}")
    print("=" * 40 + "\n")
    
    return sorted_tiles, tile_to_objects, tile_to_texture


def repack_uvs_udim_based(objects, target_udim_count):
    """Repack UVs by moving entire UDIM tiles (not individual objects)"""
    
    # Step 1: Detect all source UDIM tiles and their textures
    source_tiles, tile_objects, source_tile_to_texture = detect_source_udims(objects)
    
    if not source_tiles:
        print("No UDIM tiles found")
        return [], [], {}
    
    source_count = len(source_tiles)
    
    print(f"Repacking {source_count} source UDIM tiles into {target_udim_count} target tiles")
    print(f"Source tiles: {source_tiles}")
    
    # Show texture mapping
    for tile_num in source_tiles:
        tex_name = source_tile_to_texture.get(tile_num)
        tex_name_str = tex_name.name if tex_name else "None"
        obj_names = [obj.name for obj in tile_objects.get(tile_num, [])]
        print(f"  Source UDIM {tile_num}: texture={tex_name_str}, objects={obj_names}")
    
    # Step 2: Calculate grid layout
    tiles_per_target = math.ceil(source_count / target_udim_count)
    grid_cols = math.ceil(math.sqrt(tiles_per_target))
    grid_rows = math.ceil(tiles_per_target / grid_cols)
    
    print(f"Grid per target UDIM: {grid_cols}x{grid_rows} = {tiles_per_target} slots")
    
    # Step 3: Calculate scale and cell size - NO PADDING
    cell_size = 1.0 / grid_cols
    scale = cell_size
    
    print(f"Scale factor: {scale:.3f}, Cell size: {cell_size:.3f}, NO PADDING")
    
    # Calculate target UDIM tile layout
    target_tiles_per_row = math.ceil(math.sqrt(target_udim_count))
    
    # Step 4: Pack each source UDIM tile
    placed_objects = []
    
    for source_idx, source_tile in enumerate(source_tiles):
        # Determine target tile and position within it
        target_tile_idx = source_idx // tiles_per_target
        cell_idx = source_idx % tiles_per_target
        
        # Target UDIM position
        target_u = target_tile_idx % target_tiles_per_row
        target_v = target_tile_idx // target_tiles_per_row
        target_tile_num = 1001 + (target_v * 10) + target_u
        
        # Cell position within target UDIM
        cell_col = cell_idx % grid_cols
        cell_row = cell_idx // grid_cols
        
        # Calculate position in target UDIM (local 0-1 space) - NO PADDING
        cell_x = cell_col * cell_size
        cell_y = cell_row * cell_size
        
        # Convert to global coordinates
        global_x = target_u + cell_x
        global_y = target_v + cell_y
        
        # Get source tile position
        source_u = (source_tile - 1001) % 10
        source_v = (source_tile - 1001) // 10
        
        print(f"  Source UDIM {source_tile} → Target UDIM {target_tile_num} at cell [{cell_col},{cell_row}]")
        
        # Transform UVs for all objects using this source tile
        if source_tile in tile_objects:
            for obj in tile_objects[source_tile]:
                uv_layer = obj.data.uv_layers.active
                
                for loop in obj.data.loops:
                    uv = uv_layer.data[loop.index].uv
                    
                    # Check if this UV is in the current source tile
                    uv_tile_u = int(uv.x if uv.x != int(uv.x) or uv.x == 0 else uv.x - 0.001)
                    uv_tile_v = int(uv.y if uv.y != int(uv.y) or uv.y == 0 else uv.y - 0.001)
                    uv_tile_num = 1001 + (uv_tile_v * 10) + uv_tile_u
                    
                    if uv_tile_num == source_tile:
                        # Convert to local 0-1 space within source tile
                        local_u = uv.x - source_u
                        local_v = uv.y - source_v
                        
                        # Scale and position in target
                        uv.x = global_x + (local_u * scale)
                        uv.y = global_y + (local_v * scale)
                
                if obj not in placed_objects:
                    placed_objects.append(obj)
    
    print(f"Successfully repacked {source_count} UDIM tiles")
    print(f"Processed {len(placed_objects)} objects")
    
    # Return placed objects, source tiles list, and texture mapping for compositor
    return placed_objects, source_tiles, source_tile_to_texture


def setup_emission_for_baking(material, socket_name, texture_map):
    """Setup emission shader for baking a specific texture type"""
    if not material.use_nodes:
        return None
    
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    
    # Store original output connection
    output_node = None
    original_connection = None
    
    for node in nodes:
        if node.type == 'OUTPUT_MATERIAL':
            output_node = node
            if node.inputs['Surface'].is_linked:
                original_connection = node.inputs['Surface'].links[0]
            break
    
    if not output_node:
        return None
    
    # Find the texture for this socket
    texture_node = None
    principled = None
    
    for node in nodes:
        if node.type == 'BSDF_PRINCIPLED':
            principled = node
            break
    
    # Strategy 1: Check if this is a standard Principled BSDF socket
    is_standard_socket = socket_name in [
        'Base Color', 'Metallic', 'Roughness', 'Normal', 'Specular', 
        'Emission', 'Emission Color', 'Alpha', 'Transmission', 'Subsurface Color'
    ]
    
    if principled and is_standard_socket:
        # Find texture connected to this socket (recursively)
        if socket_name in principled.inputs and principled.inputs[socket_name].is_linked:
            # Use recursive search to find texture through any intermediate nodes
            socket = principled.inputs[socket_name]
            
            # Handle normal map
            if socket_name == "Normal" and socket.is_linked:
                from_node = socket.links[0].from_node
                if from_node.type == 'NORMAL_MAP' and from_node.inputs['Color'].is_linked:
                    texture_image = find_connected_texture_recursive(from_node.inputs['Color'])
                else:
                    texture_image = find_connected_texture_recursive(socket)
            else:
                texture_image = find_connected_texture_recursive(socket)
            
            # Find the actual texture node with this image
            if texture_image:
                for node in nodes:
                    if node.type == 'TEX_IMAGE' and node.image == texture_image:
                        texture_node = node
                        break
    
    # Strategy 2: Look for texture by name/pattern if not found or not standard socket
    if not texture_node:
        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                # Try multiple matching strategies
                match = False
                
                img_lower = node.image.name.lower()
                socket_lower = socket_name.lower().replace(' ', '')
                
                # Check if socket name matches image name pattern
                if socket_lower in img_lower.replace('_', '').replace(' ', ''):
                    match = True
                
                # Common pattern mappings
                if 'ambient' in socket_lower and 'ao' in img_lower:
                    match = True
                elif 'basecolor' in socket_lower and ('basecolor' in img_lower or 'diffuse' in img_lower):
                    match = True
                
                if match:
                    texture_node = node
                    break
    
    if not texture_node or not texture_node.image:
        print(f"    Warning: Could not find texture node for {socket_name}")
        return None
    
    # Disconnect all from output first
    # Remove all links connected to the Surface input
    for link in list(output_node.inputs['Surface'].links):
        links.remove(link)
    
    # Create emission shader
    emission = nodes.new('ShaderNodeEmission')
    emission.location = (100, 0)
    emission.name = "TEMP_EMISSION_BAKE"
    
    # Connect texture directly to emission
    links.new(texture_node.outputs['Color'], emission.inputs['Color'])
    
    # Connect emission to output
    links.new(emission.outputs['Emission'], output_node.inputs['Surface'])
    
    return {
        'emission': emission,
        'original_connection': original_connection,
        'output': output_node,
        'texture_node': texture_node
    }


def cleanup_emission_setup(material, setup_info):
    """Remove temporary emission shader and restore original connections"""
    if not setup_info or not material.use_nodes:
        return
    
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    
    # Clear current output connections
    output_node = setup_info['output']
    
    # Remove all links connected to the Surface input
    for link in list(output_node.inputs['Surface'].links):
        links.remove(link)
    
    # Remove emission node
    if setup_info['emission'] and setup_info['emission'].name in [n.name for n in nodes]:
        nodes.remove(setup_info['emission'])
    
    # Restore original connection
    if setup_info['original_connection']:
        from_socket = setup_info['original_connection'].from_socket
        to_socket = output_node.inputs.get('Surface')
        
        # Validate both sockets exist before reconnecting
        if from_socket and to_socket and from_socket.is_output:
            try:
                links.new(from_socket, to_socket)
            except Exception as e:
                print(f"  Warning: Could not restore original connection: {e}")


def calculate_lossless_resolution(objects, target_udim_count):
    """Calculate resolution based on source texture size and grid layout"""
    
    # Get source texture resolution (assume all are the same)
    source_resolution = None
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.uv_layers:
            continue
        
        tex_width, tex_height = get_object_texture_resolution(obj)
        if tex_width > 0 and tex_height > 0:
            source_resolution = max(tex_width, tex_height)  # Use the larger dimension
            print(f"  Found source texture: {tex_width}x{tex_height}")
            break
    
    if not source_resolution:
        print("  No source textures found, using default 2048")
        return 2048
    
    # Count source UDIM tiles
    source_tiles = set()
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.uv_layers:
            continue
        
        uv_layer = obj.data.uv_layers.active
        if not uv_layer:
            continue
        
        for loop in obj.data.loops:
            uv = uv_layer.data[loop.index].uv
            tile_u = int(uv.x if uv.x != int(uv.x) or uv.x == 0 else uv.x - 0.001)
            tile_v = int(uv.y if uv.y != int(uv.y) or uv.y == 0 else uv.y - 0.001)
            tile_number = 1001 + (tile_v * 10) + tile_u
            source_tiles.add(tile_number)
    
    source_count = len(source_tiles)
    if source_count == 0:
        source_count = 1
    
    # Calculate grid layout
    tiles_per_target = math.ceil(source_count / target_udim_count)
    grid_cols = math.ceil(math.sqrt(tiles_per_target))
    
    # Calculate output resolution
    # Each source UDIM becomes grid_cols × grid_cols cells
    # So we need source_resolution × grid_cols
    output_resolution = source_resolution * grid_cols
    
    # Round up to nearest power of 2
    output_resolution = 2 ** math.ceil(math.log2(output_resolution))
    
    # Clamp to reasonable range
    output_resolution = max(512, min(8192, output_resolution))
    
    print(f"Lossless calculation:")
    print(f"  Source: {source_resolution}x{source_resolution}")
    print(f"  {source_count} source UDIMs → {target_udim_count} target UDIMs")
    print(f"  Grid: {grid_cols}x{grid_cols} per target")
    print(f"  Output: {output_resolution}x{output_resolution}")
    
    return output_resolution


def composite_textures_with_pil(objects, socket_name, texture_map, resolution, output_path, source_tiles, source_tile_to_texture):
    """Composite full UDIM tiles - matching the UDIM-based UV repacking"""
    import sys
    import time
    start_time = time.time()
    
    try:
        from PIL import Image
    except ImportError:
        print("ERROR: PIL/Pillow not available")
        return None
    
    print(f"\n{'='*60}")
    print(f"COMPOSITING: {socket_name}")
    print(f"{'='*60}")
    sys.stdout.flush()
    
    print(f"Using PIL to composite {socket_name} textures...")
    print(f"Source UDIMs to composite: {source_tiles}")
    sys.stdout.flush()
    
    # Verify texture mapping
    for tile_num in source_tiles:
        if tile_num in source_tile_to_texture:
            print(f"  Source UDIM {tile_num} → {source_tile_to_texture[tile_num].name}")
    sys.stdout.flush()
    
    # Load all source textures as PIL images using the provided mapping
    source_udim_to_pil = {}
    print(f"\nLoading {len(source_tiles)} source UDIM tiles...")
    
    # Cache for UDIM texture sets to avoid re-saving
    udim_cache = {}  # image.name -> {tile_num: pil_image}
    
    for idx, tile_num in enumerate(source_tiles):
        print(f"  [{idx+1}/{len(source_tiles)}] Loading UDIM {tile_num}...", end=" ", flush=True)
        
        if tile_num not in source_tile_to_texture:
            print("✗ No texture mapped")
            continue
            
        blender_image = source_tile_to_texture[tile_num]
        
        try:
            import tempfile
            
            # Check if this is a UDIM tiled image
            if hasattr(blender_image, 'source') and blender_image.source == 'TILED':
                # UDIM tiled image - use existing filepath, don't try to save
                if not blender_image.filepath:
                    print(f"✗ No filepath set")
                    continue
                
                # Check cache first
                if blender_image.name in udim_cache:
                    if tile_num in udim_cache[blender_image.name]:
                        source_udim_to_pil[tile_num] = udim_cache[blender_image.name][tile_num]
                        print(f"✓ From cache")
                        continue
                
                # Get the base filepath and construct tile path
                base_filepath = blender_image.filepath_raw
                
                # Replace <UDIM> marker with actual tile number
                tile_filepath = base_filepath.replace('<UDIM>', str(tile_num))
                
                # Make absolute path
                tile_filepath = bpy.path.abspath(tile_filepath)
                
                print(f"Loading from disk: {tile_filepath}...", end=" ", flush=True)
                
                if not os.path.exists(tile_filepath):
                    print(f"✗ File not found")
                    continue
                
                # Load directly from disk with PIL
                pil_img = Image.open(tile_filepath)
                # Convert to RGB (no alpha)
                if pil_img.mode == 'RGBA':
                    rgb_img = Image.new('RGB', pil_img.size, (255, 255, 255))
                    rgb_img.paste(pil_img, mask=pil_img.split()[3])
                    pil_img = rgb_img
                elif pil_img.mode != 'RGB':
                    pil_img = pil_img.convert('RGB')
                
                source_udim_to_pil[tile_num] = pil_img
                
                # Cache it
                if blender_image.name not in udim_cache:
                    udim_cache[blender_image.name] = {}
                udim_cache[blender_image.name][tile_num] = pil_img
                
                print(f"✓ {pil_img.size[0]}x{pil_img.size[1]}")
                
                # Pre-load and cache other tiles from this set
                for other_tile in source_tiles:
                    if other_tile != tile_num and other_tile not in udim_cache[blender_image.name]:
                        other_filepath = base_filepath.replace('<UDIM>', str(other_tile))
                        other_filepath = bpy.path.abspath(other_filepath)
                        if os.path.exists(other_filepath):
                            other_img = Image.open(other_filepath)
                            if other_img.mode == 'RGBA':
                                rgb_other = Image.new('RGB', other_img.size, (255, 255, 255))
                                rgb_other.paste(other_img, mask=other_img.split()[3])
                                other_img = rgb_other
                            elif other_img.mode != 'RGB':
                                other_img = other_img.convert('RGB')
                            udim_cache[blender_image.name][other_tile] = other_img
                            print(f"      + Cached tile {other_tile}")
            else:
                # Regular single image - load directly from filepath if available
                if blender_image.filepath:
                    tile_filepath = bpy.path.abspath(blender_image.filepath)
                    
                    if os.path.exists(tile_filepath):
                        print(f"Loading from disk: {tile_filepath}...", end=" ", flush=True)
                        pil_img = Image.open(tile_filepath)
                    else:
                        print(f"✗ File not found: {tile_filepath}")
                        continue
                else:
                    # No filepath - try to save from memory
                    if not blender_image.has_data:
                        print(f"✗ Image has no pixel data and no filepath")
                        continue
                    
                    temp_path = tempfile.mktemp(suffix='.png')
                    blender_image.filepath_raw = temp_path
                    blender_image.file_format = 'PNG'
                    blender_image.save()
                    pil_img = Image.open(temp_path)
                
                # Convert to RGB (no alpha)
                if pil_img.mode == 'RGBA':
                    rgb_img = Image.new('RGB', pil_img.size, (255, 255, 255))
                    rgb_img.paste(pil_img, mask=pil_img.split()[3])
                    pil_img = rgb_img
                elif pil_img.mode != 'RGB':
                    pil_img = pil_img.convert('RGB')
                
                source_udim_to_pil[tile_num] = pil_img
                print(f"✓ {pil_img.size[0]}x{pil_img.size[1]}")
        except Exception as e:
            print(f"✗ ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    if not source_udim_to_pil:
        print("\n✗ No textures loaded - ABORTING")
        sys.stdout.flush()
        return None
    
    print(f"\n✓ Successfully loaded {len(source_udim_to_pil)}/{len(source_tiles)} textures")
    sys.stdout.flush()
    
    source_count = len(source_tiles)
    
    # Detect target UDIMs from repacked UVs
    target_tiles = set()
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.uv_layers:
            continue
        uv_layer = obj.data.uv_layers.active
        for loop in obj.data.loops:
            uv = uv_layer.data[loop.index].uv
            tile_u = int(uv.x if uv.x != int(uv.x) or uv.x == 0 else uv.x - 0.001)
            tile_v = int(uv.y if uv.y != int(uv.y) or uv.y == 0 else uv.y - 0.001)
            tile_num = 1001 + (tile_v * 10) + tile_u
            target_tiles.add(tile_num)
    
    target_tiles = sorted(list(target_tiles))
    target_udim_count = len(target_tiles)
    
    print(f"  {source_count} source UDIMs → {target_udim_count} target UDIMs")
    
    # Calculate grid layout (must match UV repacking exactly)
    tiles_per_target = math.ceil(source_count / target_udim_count)
    grid_cols = math.ceil(math.sqrt(tiles_per_target))
    grid_rows = math.ceil(tiles_per_target / grid_cols)
    
    # NO PADDING - edge to edge
    cell_size = 1.0 / grid_cols
    
    print(f"  Grid: {grid_cols}x{grid_rows}, cell size: {cell_size:.3f}, NO PADDING")
    
    # Create blank target tile images (RGB only, no alpha for opaque background)
    tile_images = {}
    for tile_num in target_tiles:
        # Use RGB mode with gray background - no transparency
        img = Image.new('RGB', (resolution, resolution), (128, 128, 128))
        tile_images[tile_num] = img
    
    # Composite: place each source UDIM into target grid
    target_tiles_per_row = math.ceil(math.sqrt(target_udim_count))
    
    print(f"\nCompositing {source_count} tiles into grid...")
    
    for source_idx, source_tile in enumerate(source_tiles):
        print(f"  [{source_idx+1}/{source_count}] UDIM {source_tile}...", end=" ", flush=True)
        
        if source_tile not in source_udim_to_pil:
            print("✗ No PIL image")
            continue
        
        src_img = source_udim_to_pil[source_tile]
        
        # Calculate target position (matching UV repacking)
        target_tile_idx = source_idx // tiles_per_target
        cell_idx = source_idx % tiles_per_target
        
        target_u = target_tile_idx % target_tiles_per_row
        target_v = target_tile_idx // target_tiles_per_row
        target_tile_num = 1001 + (target_v * 10) + target_u
        
        cell_col = cell_idx % grid_cols
        cell_row = cell_idx // grid_cols
        
        # Position in target (0-1) - NO PADDING
        # CRITICAL: Flip Y-axis because PIL has Y=0 at top, Blender UV has Y=0 at bottom
        cell_x = cell_col * cell_size
        cell_y_uv = cell_row * cell_size  # UV space (0 at bottom)
        cell_y_pil = (1.0 - cell_y_uv - cell_size)  # PIL space (0 at top) - FLIPPED!
        
        # Convert to pixels
        px = int(cell_x * resolution)
        py = int(cell_y_pil * resolution)
        cell_size_px = int(cell_size * resolution)
        
        # Resize source tile to cell size and paste
        src_resized = src_img.resize((cell_size_px, cell_size_px), Image.LANCZOS)
        
        if target_tile_num in tile_images:
            tile_images[target_tile_num].paste(src_resized, (px, py))
            print(f"✓ → Target {target_tile_num} cell[{cell_col},{cell_row}] @({px},{py})")
        else:
            print(f"✗ Target tile {target_tile_num} not found!")
    
    print(f"\n✓ Composited all tiles")
    sys.stdout.flush()
    
    # Save all target tiles
    saved_tiles = {}
    print(f"\nSaving {len(tile_images)} target tiles...")
    for tile_num, img in tile_images.items():
        tile_path = output_path.replace('.png', f'.{tile_num}.png')
        img.save(tile_path)
        saved_tiles[tile_num] = tile_path
        print(f"  Saved {tile_path}")
    
    elapsed = time.time() - start_time
    print(f"\n✓ Compositing complete in {elapsed:.1f}s")
    print(f"{'='*60}\n")
    sys.stdout.flush()
    
    return saved_tiles

def load_udim_image_from_tiles(tile_paths, socket_name, resolution, base_name, source_tile_to_texture, texture_map):
    """Load PIL-generated tiles as a proper UDIM tiled image in Blender"""
    
    image_name = f"{base_name}_{socket_name.replace(' ', '_')}"
    
    # Remove existing images
    for img in list(bpy.data.images):
        if img.name.startswith(image_name):
            bpy.data.images.remove(img)
    
    # Determine if this is a color or non-color texture
    is_color_data = socket_name in ['Base Color', 'Emission Color', 'Emission', 'Subsurface Color']
    
    # Get original color space from source textures for this specific socket type
    original_colorspace = 'sRGB'  # Default for color data
    if is_color_data:
        # Look at the source textures for THIS specific socket type
        if socket_name in texture_map:
            for obj_textures in texture_map[socket_name]:
                if 'image' in obj_textures and obj_textures['image']:
                    source_img = obj_textures['image']
                    if hasattr(source_img, 'colorspace_settings'):
                        original_colorspace = source_img.colorspace_settings.name
                        print(f"  Detected original color space for {socket_name}: {original_colorspace}")
                        break
    
    # Get tile numbers
    tile_numbers = sorted(tile_paths.keys())
    
    print(f"Loading UDIM image from {len(tile_numbers)} tiles...")
    
    # Create UDIM tiled image
    baked_image = bpy.data.images.new(
        name=image_name,
        width=resolution,
        height=resolution,
        alpha=False,  # PIL generates RGB, not RGBA
        float_buffer=False,
        is_data=not is_color_data,
        tiled=True
    )
    
    # Configure color space
    if not is_color_data:
        baked_image.colorspace_settings.name = 'Non-Color'
        print(f"  Set color space: Non-Color (data texture)")
    else:
        # Preserve original color space for color data, with validation
        if original_colorspace and original_colorspace.strip():
            baked_image.colorspace_settings.name = original_colorspace
            print(f"  Set color space: {original_colorspace} (preserved from source)")
        else:
            # Fallback to sRGB if colorspace is empty/invalid
            baked_image.colorspace_settings.name = 'sRGB'
            print(f"  Set color space: sRGB (fallback, source had empty colorspace)")
    
    # Set up UDIM tiles
    for idx, tile_number in enumerate(tile_numbers):
        if idx == 0:
            baked_image.tiles[0].number = tile_number
        else:
            baked_image.tiles.new(tile_number=tile_number)
    
    # Set the source to the first tile file with UDIM marker
    first_path = tile_paths[tile_numbers[0]]
    udim_path = first_path.replace(f'.{tile_numbers[0]}.png', '.<UDIM>.png')
    baked_image.filepath_raw = udim_path
    baked_image.source = 'TILED'
    
    # Reload to load all tiles
    try:
        baked_image.reload()
        print(f"  ✓ Loaded UDIM image: {udim_path}")
    except Exception as e:
        print(f"  Warning: Could not reload UDIM image: {e}")
    
    return baked_image


def bake_combined_texture(objects, socket_name, texture_map, resolution, num_udims, output_path, base_name, source_tiles, socket_tile_to_texture):
    """Bake combined texture for a specific socket type with UDIM support"""
    
    print(f"\n{'='*60}")
    print(f"BAKING: {socket_name}")
    print(f"{'='*60}")
    sys.stdout.flush()
    
    if not socket_tile_to_texture:
        print(f"WARNING: No textures found for {socket_name}, falling back to Blender baking")
    else:
        print(f"Using {len(socket_tile_to_texture)} pre-detected textures for {socket_name}")
    sys.stdout.flush()
    
    # Try PIL-based compositing first (more reliable)
    if socket_tile_to_texture:  # Only try PIL if we found textures
        try:
            from PIL import Image
            tile_paths = composite_textures_with_pil(objects, socket_name, texture_map, resolution, output_path, source_tiles, socket_tile_to_texture)
            if tile_paths:
                # Load the generated tiles back as a UDIM image in Blender
                baked_image = load_udim_image_from_tiles(tile_paths, socket_name, resolution, base_name, socket_tile_to_texture, texture_map)
                return baked_image
        except ImportError:
            print(f"PIL not available, using Blender baking...")
        except Exception as e:
            print(f"PIL compositing failed: {e}, falling back to Blender baking...")
            import traceback
            traceback.print_exc()
    
    # Fall back to Blender baking
    print(f"Using Blender baking for {socket_name}...")
    
    # First, detect which UDIM tiles are actually being used after repacking
    used_tiles = set()
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.uv_layers:
            continue
        
        uv_layer = obj.data.uv_layers.active
        if not uv_layer:
            continue
        
        for loop in obj.data.loops:
            uv = uv_layer.data[loop.index].uv
            tile_u = int(uv.x if uv.x != int(uv.x) or uv.x == 0 else uv.x - 0.001)
            tile_v = int(uv.y if uv.y != int(uv.y) or uv.y == 0 else uv.y - 0.001)
            tile_number = 1001 + (tile_v * 10) + tile_u
            used_tiles.add(tile_number)
    
    if not used_tiles:
        print(f"Warning: No UVs found in valid UDIM range for {socket_name}")
        used_tiles = {1001}  # Default to at least tile 1001
    
    used_tiles = sorted(list(used_tiles))
    print(f"Detected used UDIM tiles: {used_tiles}")
    
    # Create images for each UDIM tile
    image_name = f"Combined_{socket_name.replace(' ', '_')}"
    
    # Remove existing images
    for img in list(bpy.data.images):
        if img.name.startswith(image_name):
            bpy.data.images.remove(img)
    
    # Determine if this is a color or non-color texture
    is_color_data = socket_name in ['Base Color', 'Emission Color', 'Emission', 'Subsurface Color']
    
    # Create UDIM tiled image (no alpha for opaque background)
    baked_image = bpy.data.images.new(
        name=image_name,
        width=resolution,
        height=resolution,
        alpha=False,
        float_buffer=False,
        is_data=not is_color_data,
        tiled=True
    )
    
    # Configure color space
    if not is_color_data:
        baked_image.colorspace_settings.name = 'Non-Color'
    
    # Add only the UDIM tiles that are actually used
    for idx, tile_number in enumerate(used_tiles):
        if idx == 0:
            # First tile already exists, just set the number
            baked_image.tiles[0].number = tile_number
        else:
            # Add additional tiles
            baked_image.tiles.new(tile_number=tile_number)
    
    print(f"Created UDIM image with tiles: {used_tiles}")
    
    # CRITICAL: Force UDIM tile initialization by writing pixels
    # UDIM tiles need pixel buffers allocated before baking
    print(f"Initializing UDIM tile buffers...")
    
    # Method 1: Try to allocate pixels for each tile
    for tile in baked_image.tiles:
        try:
            # Access pixels array to force allocation
            # Initialize with neutral gray (0.5, 0.5, 0.5, 1.0)
            pixel_count = resolution * resolution * 4  # RGBA
            pixels = [0.5] * (pixel_count - pixel_count // 4) + [1.0] * (pixel_count // 4)
            
            # Try to set pixels (this might not work directly on UDIM tiles)
            baked_image.pixels[:] = pixels
            print(f"  Initialized tile {tile.number}")
        except Exception as e:
            print(f"  Could not directly initialize tile {tile.number}: {e}")
    
    # Method 2: Try to generate the image
    try:
        baked_image.update()
        baked_image.reload()
    except:
        pass
    
    # Force save to disk to allocate buffers (always save)
    import os
    base_path = output_path.replace('.png', '')
    
    # Create output directory if it doesn't exist
    output_dir_path = os.path.dirname(base_path)
    if output_dir_path and not os.path.exists(output_dir_path):
        os.makedirs(output_dir_path)
    
    # Save with UDIM marker (ALWAYS set this, not just when creating dir)
    udim_filepath = f"{base_path}.<UDIM>.png"
    baked_image.filepath_raw = udim_filepath
    baked_image.file_format = 'PNG'
    
    try:
        baked_image.save()
        print(f"  ✓ UDIM tiles initialized and saved to: {udim_filepath}")
    except Exception as e:
        print(f"  ✗ Warning: Could not pre-save UDIM tiles: {e}")
        print(f"     This may cause 'Uninitialized image' error during baking")
    
    print(f"UDIM tile initialization complete")
    
    # Setup materials for baking
    emission_setups = []
    temp_bake_nodes = []
    materials_with_setup = []
    
    print(f"Setting up materials for baking {socket_name}...")
    
    for obj in objects:
        if obj.type != 'MESH':
            continue
        
        for mat_slot in obj.material_slots:
            if not mat_slot.material or not mat_slot.material.use_nodes:
                print(f"  Skipping {obj.name}: material has no nodes")
                continue
            
            # Skip if already processed this material
            if mat_slot.material in materials_with_setup:
                continue
            
            print(f"  Processing material: {mat_slot.material.name} on {obj.name}")
            
            # Setup emission for this material
            setup_info = setup_emission_for_baking(mat_slot.material, socket_name, texture_map)
            if setup_info:
                emission_setups.append((mat_slot.material, setup_info))
                materials_with_setup.append(mat_slot.material)
                print(f"    ✓ Set up emission (texture: {setup_info['texture_node'].image.name})")
            else:
                print(f"    ✗ WARNING: Could not setup emission - no texture found for {socket_name}")
            
            # Add bake target image node
            nodes = mat_slot.material.node_tree.nodes
            img_node = nodes.new('ShaderNodeTexImage')
            img_node.image = baked_image
            img_node.name = "TEMP_BAKE_TARGET"
            nodes.active = img_node
            img_node.select = True
            temp_bake_nodes.append((mat_slot.material, img_node))
    
    if not emission_setups:
        print(f"Warning: No emission setups created for {socket_name}")
        # Still return the image, but it might be empty
        return baked_image
    
    print(f"Set up {len(emission_setups)} materials for baking {socket_name}")
    
    # Configure render settings
    original_engine = bpy.context.scene.render.engine
    original_samples = bpy.context.scene.cycles.samples
    original_device = bpy.context.scene.cycles.device
    
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.cycles.samples = 1
    bpy.context.scene.cycles.device = 'CPU'  # Force CPU for reliability
    
    # Bake settings
    bpy.context.scene.render.bake.use_pass_direct = False
    bpy.context.scene.render.bake.use_pass_indirect = False
    bpy.context.scene.render.bake.use_pass_color = True  # Enable to capture emission
    bpy.context.scene.render.bake.margin = 16
    bpy.context.scene.render.bake.use_clear = True
    bpy.context.scene.render.bake.use_selected_to_active = False
    
    # Select objects for baking
    bpy.ops.object.select_all(action='DESELECT')
    bake_count = 0
    for obj in objects:
        if obj.type == 'MESH':
            obj.select_set(True)
            bake_count += 1
    
    print(f"Selected {bake_count} objects for baking")
    
    if not bpy.context.selected_objects:
        print(f"ERROR: No objects selected for baking!")
        return baked_image
    
    # Set active object and ensure we're in object mode
    bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
    
    # Make sure we're in object mode
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    
    # Bake
    try:
        print(f"Starting bake for {socket_name}...")
        print(f"  Bake type: EMIT")
        print(f"  Target image: {baked_image.name}")
        print(f"  Resolution: {resolution}x{resolution}")
        print(f"  UDIM tiles: {[t.number for t in baked_image.tiles]}")
        
        bpy.ops.object.bake(type='EMIT')
        print(f"✓ Successfully baked {socket_name}")
    except Exception as e:
        print(f"✗ Baking error for {socket_name}: {e}")
        import traceback
        traceback.print_exc()
        
        # Try to provide helpful error message
        if "Uninitialized" in str(e):
            print(f"  ERROR: Image tiles not initialized. This may be a Blender bug with UDIM baking.")
        elif "No active image" in str(e):
            print(f"  ERROR: No active image node found in materials.")
    
    # Pack the image so it stays in memory
    try:
        baked_image.pack()
        print(f"Baked image packed in memory")
    except:
        pass
    
    # Save/update images (always save)
    try:
        # Re-save after baking to capture baked data
        baked_image.save()
        print(f"Saved baked UDIM tiles to: {baked_image.filepath_raw}")
    except Exception as e:
        print(f"Error saving {socket_name}: {e}")
    
    # Cleanup
    for material, setup_info in emission_setups:
        cleanup_emission_setup(material, setup_info)
    
    for material, node in temp_bake_nodes:
        if node.name in [n.name for n in material.node_tree.nodes]:
            material.node_tree.nodes.remove(node)
    
    # Restore settings
    bpy.context.scene.render.engine = original_engine
    bpy.context.scene.cycles.samples = original_samples
    bpy.context.scene.cycles.device = original_device
    
    return baked_image


def create_combined_material(baked_images, texture_map, base_name):
    """Reuse an existing material and update texture references"""
    
    print(f"\n=== UPDATING MATERIAL WITH COMBINED TEXTURES ===")
    print(f"Baked images: {list(baked_images.keys())}")
    
    # Find a source material from texture_map
    source_material = None
    for socket_name, entries in texture_map.items():
        if entries:
            source_material = entries[0]['material']
            break
    
    if not source_material:
        print("ERROR: No source material found")
        return None
    
    print(f"Reusing existing material: {source_material.name}")
    
    if not source_material.use_nodes:
        print("ERROR: Source material doesn't use nodes")
        return None
    
    nodes = source_material.node_tree.nodes
    
    print(f"Available baked images: {list(baked_images.keys())}")
    
    # Update all texture nodes to use the new combined images
    print(f"Scanning {len(nodes)} nodes for texture updates...")
    for node in nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            old_image_name = node.image.name.split('.')[0].replace('_', ' ').title()
            old_colorspace = node.image.colorspace_settings.name
            print(f"  Node has image: {node.image.name} → normalized to: '{old_image_name}'")
            print(f"    Original colorspace: {old_colorspace}")
            
            # Try to find matching baked image
            if old_image_name in baked_images:
                new_image = baked_images[old_image_name]
                new_colorspace = new_image.colorspace_settings.name
                print(f"    ✓ MATCH! Updating to {new_image.name}")
                print(f"    New image colorspace (before fix): {new_colorspace}")
                
                # Fix colorspace if different
                if new_colorspace != old_colorspace:
                    try:
                        new_image.colorspace_settings.name = old_colorspace
                        print(f"    → Fixed colorspace to: {old_colorspace}")
                    except Exception as e:
                        print(f"    ✗ Could not set colorspace: {e}")
                
                node.image = new_image
                print(f"    ✓ Updated - final colorspace: {node.image.colorspace_settings.name}")
            else:
                print(f"    ✗ No match found for '{old_image_name}' (keeping original)")
    
    print(f"✓ Material '{source_material.name}' updated successfully")
    print("=" * 40 + "\n")
    return source_material


class BB_OT_CombineTextures(bpy.types.Operator):
    bl_idname = "object.bb_combine_textures"
    bl_label = "Combine Textures"
    bl_description = "Combine textures from selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.bb_texture_combine_props
        selected_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
        
        if not selected_objects:
            self.report({'ERROR'}, "No mesh objects selected")
            return {'CANCELLED'}
        
        # CRITICAL: Pack and reload all textures FIRST to ensure they have image data
        print("\n=== PREPARING TEXTURES ===")
        for img in bpy.data.images:
            if img.source == 'TILED' and img.packed_file is None:
                try:
                    # Try to pack if not already packed
                    img.pack()
                    print(f"Packed: {img.name}")
                except:
                    # If can't pack, try to reload from filepath
                    if img.filepath:
                        try:
                            img.reload()
                            print(f"Reloaded: {img.name}")
                        except:
                            pass
        
        # Analyze materials
        self.report({'INFO'}, "Analyzing materials...")
        texture_map = analyze_materials(selected_objects)
        
        if not texture_map:
            self.report({'ERROR'}, "No textures found in selected objects")
            return {'CANCELLED'}
        
        self.report({'INFO'}, f"Found {len(texture_map)} texture types: {', '.join(texture_map.keys())}")
        
        # CRITICAL: Detect texture mappings for ALL sockets BEFORE repacking
        # After repacking, UV positions change and we lose track of original textures
        print("\n=== PRE-DETECTING ALL SOCKET TEXTURES ===")
        socket_texture_mappings = {}  # socket_name -> {tile_num: texture}
        
        for socket_name in texture_map.keys():
            print(f"Pre-detecting {socket_name}...")
            _, _, tile_to_texture = detect_source_udims_for_socket(selected_objects, socket_name)
            socket_texture_mappings[socket_name] = tile_to_texture
            print(f"  → Mapped {len(tile_to_texture)} UDIMs")
        
        print("=" * 60 + "\n")
        
        # Check current UDIM count
        current_udims = get_current_udim_count(selected_objects)
        self.report({'INFO'}, f"Current UDIMs: {current_udims}")
        
        # Determine output resolution BEFORE repacking (needs original UV bounds)
        if props.use_lossless:
            self.report({'INFO'}, "Calculating lossless resolution...")
            output_resolution = calculate_lossless_resolution(selected_objects, props.target_udim_count)
            self.report({'INFO'}, f"Using calculated resolution: {output_resolution}x{output_resolution}")
        else:
            output_resolution = props.output_resolution
        
        # Repack UVs
        self.report({'INFO'}, f"Repacking UVs to {props.target_udim_count} tiles...")
        placed_objects, source_tiles, source_tile_to_texture = repack_uvs_udim_based(selected_objects, props.target_udim_count)
        
        if not placed_objects:
            self.report({'ERROR'}, "Failed to pack UVs - no objects were successfully placed. Try increasing Target UDIMs.")
            return {'CANCELLED'}
        
        if len(placed_objects) < len(selected_objects):
            failed_count = len(selected_objects) - len(placed_objects)
            self.report({'WARNING'}, f"{failed_count} of {len(selected_objects)} objects could not be packed! Increase 'Target UDIMs' or reduce 'Texture Resolution'.")
            self.report({'WARNING'}, f"Only {len(placed_objects)} objects will be combined.")
        
        # Use only successfully placed objects for baking
        objects_to_bake = placed_objects
        
        # Generate unique name for textures - ALWAYS use timestamp for uniqueness
        import time
        timestamp = str(int(time.time() * 1000))  # milliseconds for uniqueness
        
        if props.combine_objects and len(objects_to_bake) > 0:
            # Use first object's name + timestamp
            first_obj_name = objects_to_bake[0].name.replace('.', '_')
            base_name = f"{first_obj_name}_{timestamp}"
        else:
            # Just use timestamp
            base_name = f"TextureSet_{timestamp}"
        
        self.report({'INFO'}, f"Output name: {base_name}")
        
        # Setup output directory
        output_dir = bpy.path.abspath(f"//combined_textures/{base_name}/")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        # Bake combined textures
        self.report({'INFO'}, "Baking combined textures...")
        baked_images = {}
        
        for socket_name in texture_map.keys():
            output_path = os.path.join(output_dir, f"{base_name}_{socket_name.replace(' ', '_')}.png")
            self.report({'INFO'}, f"Baking {socket_name}...")
            
            # Use pre-detected texture mapping for this socket
            socket_tile_to_texture = socket_texture_mappings.get(socket_name, {})
            
            img = bake_combined_texture(
                objects_to_bake,  # Use only placed objects
                socket_name,
                texture_map,
                output_resolution,  # Use calculated resolution
                props.target_udim_count,
                output_path,
                base_name,  # Pass base name for image naming
                source_tiles,  # Pass source tile list for compositor
                socket_tile_to_texture  # Pass socket-specific texture mapping (pre-detected)
            )
            baked_images[socket_name] = img
        
        # Create/assign materials
        if props.combine_objects:
            print(f"\n=== COMBINE OBJECTS MODE ===")
            self.report({'INFO'}, "Creating combined material...")
            combined_mat = create_combined_material(baked_images, texture_map, base_name)
            
            if not combined_mat:
                self.report({'ERROR'}, "Failed to create combined material")
                return {'CANCELLED'}
            
            self.report({'INFO'}, f"Created material: {combined_mat.name}")
            print(f"Material created: {combined_mat.name}")
            
            # Join all objects into one
            print(f"\nJoining {len(objects_to_bake)} objects into one...")
            
            # Make sure we're in object mode
            if context.object and context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            
            # Deselect all
            bpy.ops.object.select_all(action='DESELECT')
            
            # Select all objects to bake
            for obj in objects_to_bake:
                obj.select_set(True)
            
            # Set first object as active
            if objects_to_bake:
                context.view_layer.objects.active = objects_to_bake[0]
                print(f"Active object: {objects_to_bake[0].name}")
                
                # Join all selected objects into the active one
                try:
                    bpy.ops.object.join()
                    combined_obj = context.view_layer.objects.active
                    print(f"✓ Joined into: {combined_obj.name}")
                    
                    # Assign material to combined object
                    combined_obj.data.materials.clear()
                    combined_obj.data.materials.append(combined_mat)
                    print(f"✓ Assigned material: {combined_mat.name}")
                    
                    self.report({'INFO'}, f"✓ Combined {len(objects_to_bake)} objects into '{combined_obj.name}'")
                    self.report({'INFO'}, f"✓ Assigned material '{combined_mat.name}'")
                except Exception as e:
                    print(f"✗ ERROR joining objects: {e}")
                    import traceback
                    traceback.print_exc()
                    self.report({'ERROR'}, f"Failed to join objects: {e}")
                    return {'CANCELLED'}
            
            print("=" * 40 + "\n")
        else:
            self.report({'INFO'}, "Updating individual materials...")
            # Update each object's material to use combined textures
            for obj in objects_to_bake:
                for mat_slot in obj.material_slots:
                    if not mat_slot.material or not mat_slot.material.use_nodes:
                        continue
                    
                    nodes = mat_slot.material.node_tree.nodes
                    
                    # Find and update texture nodes
                    for node in nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            # Save original colorspace
                            old_colorspace = node.image.colorspace_settings.name
                            
                            # Strategy 1: Try to match by checking direct connections
                            matched = False
                            new_image = None
                            for output in node.outputs:
                                for link in output.links:
                                    socket_name = link.to_socket.name
                                    
                                    # Handle normal map case
                                    if link.to_node.type == 'NORMAL_MAP':
                                        socket_name = "Normal"
                                    
                                    if socket_name in baked_images:
                                        new_image = baked_images[socket_name]
                                        matched = True
                                        break
                                if matched:
                                    break
                            
                            # Strategy 2: If not matched by connection, try matching by image name
                            if not matched:
                                # Check if this image name matches any baked texture name
                                img_name_normalized = node.image.name.split('.')[0].replace('_', ' ').title()
                                if img_name_normalized in baked_images:
                                    new_image = baked_images[img_name_normalized]
                                    matched = True
                            
                            # Update the image and preserve colorspace
                            if matched and new_image:
                                # Fix colorspace if different
                                if new_image.colorspace_settings.name != old_colorspace:
                                    try:
                                        new_image.colorspace_settings.name = old_colorspace
                                    except:
                                        pass
                                node.image = new_image
        
        self.report({'INFO'}, f"Textures saved to: {output_dir}")
        
        self.report({'INFO'}, "Texture combining complete!")
        return {'FINISHED'}


classes = (
    BBTextureCombineProperties,
    BBTextureCombinePanel,
    BB_OT_CombineTextures,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.bb_texture_combine_props = bpy.props.PointerProperty(
        type=BBTextureCombineProperties
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.bb_texture_combine_props


if __name__ == "__main__":
    register()
