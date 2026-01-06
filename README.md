# BB Texture Combine

A powerful Blender addon for combining and repacking UDIM textures, optimizing texture atlases for better performance in games and real-time applications.

## Features

- **UDIM Texture Combining**: Automatically combines multiple UDIM tiles into fewer tiles
- **Smart Texture Detection**: Detects ALL textures in materials, including non-directly connected textures (AO mixed with BaseColor, etc.)
- **Fast PIL Compositing**: Uses Python Imaging Library for rapid texture compositing (2-7 seconds per texture)
- **Automatic UV Repacking**: Intelligently repacks UVs to match the new UDIM layout
- **Colorspace Preservation**: Maintains correct colorspace settings (sRGB, Non-Color, etc.) throughout the process
- **Multiple Material Support**: Works with complex material setups including Mix nodes, ColorRamp, and custom connections
- **Boundary-Aware Detection**: Correctly identifies which UDIM tile each object belongs to using UV bounding box center
- **Two Workflow Modes**:
  - **Individual Materials**: Updates each object's material separately
  - **Combined Objects**: Joins objects and creates a single combined material

## Installation

### Method 1: Install from ZIP (Recommended)

1. Download the latest `bb_texture_combine_modified.zip` from releases
2. In Blender, go to `Edit > Preferences > Add-ons`
3. Click `Install...` and select the downloaded ZIP file
4. Enable the addon by checking the checkbox next to "BB Texture Combine"

### Method 2: Manual Installation

1. Extract the ZIP file
2. Copy the `bb_texture_combine_ext` folder to your Blender extensions directory:
   - **Windows**: `%APPDATA%\Blender Foundation\Blender\[version]\extensions\user_default\`
   - **macOS**: `~/Library/Application Support/Blender/[version]/extensions/user_default/`
   - **Linux**: `~/.config/blender/[version]/extensions/user_default/`
3. Restart Blender and enable the addon in Preferences

## Usage

### Basic Workflow

1. **Select Objects**: Select all mesh objects you want to combine textures for
2. **Open Panel**: Find the "BB Texture Combine" panel in the 3D Viewport sidebar (press `N` to show sidebar)
3. **Configure Options**:
   - **Target UDIM Count**: Number of UDIM tiles you want in the final result (default: 2)
   - **Combine Objects**: Enable to join objects into a single mesh with one material
4. **Click "Combine Textures"**: The addon will:
   - Analyze all materials and detect textures
   - Determine which UDIM tiles are currently used
   - Repack UVs to fit into the target UDIM count
   - Composite textures using PIL for speed
   - Update materials with the new combined textures

### Output

Combined textures are saved to:
```
//combined_textures/[TextureSet_timestamp]/
├── TextureSet_[timestamp]_Base_Color.1001.png
├── TextureSet_[timestamp]_Base_Color.1002.png
├── TextureSet_[timestamp]_Metallic.1001.png
├── TextureSet_[timestamp]_Normal.1001.png
└── ...
```

## How It Works

### 1. Material Analysis
The addon scans all selected objects and:
- Detects textures connected to Principled BSDF inputs
- Finds ALL texture nodes in the material (including those in Mix nodes)
- Maps each texture to its socket type or image name

### 2. UDIM Detection
For each object:
- Calculates the UV bounding box
- Determines the primary UDIM tile based on the bounding box center
- Handles edge cases where UVs touch UDIM boundaries

### 3. UV Repacking
- Calculates optimal grid layout (e.g., 13 tiles → 3×3 grid in 2 UDIMs)
- Scales and repositions UVs to fit the new layout
- Maintains relative positioning within each tile

### 4. Texture Compositing
**Fast Path (PIL - default)**:
- Loads source UDIM tiles into memory
- Composites them into the new grid layout
- Handles Y-axis flip (PIL top=0, Blender bottom=0)
- Saves combined tiles to disk
- Typical speed: 2-7 seconds per texture type

**Fallback Path (Blender Baking)**:
- Used if PIL compositing fails
- Sets up emission materials
- Bakes textures using Cycles
- Slower but more reliable for complex cases

### 5. Material Update
- **Individual Materials Mode**: Updates texture references in each existing material
- **Combined Objects Mode**: Reuses one material and updates all texture references
- Preserves colorspace settings, connections, and custom nodes

## Requirements

- Blender 4.0 or later (tested on Blender 5.0)
- Python 3.x
- Pillow (PIL) - included with Blender

## Supported Texture Types

The addon automatically detects and processes:
- Base Color / Diffuse / Albedo
- Metallic
- Roughness
- Normal Maps
- Ambient Occlusion (AO)
- Emission / Emissive
- Alpha / Opacity
- Specular
- Any custom-named textures

## Tips & Best Practices

### Performance
- Use **Target UDIM Count** wisely: fewer UDIMs = faster, but lower texture density
- For high-poly meshes, UV repacking may take a moment - this is normal

### Texture Quality
- Source textures should be square and power-of-2 (512, 1024, 2048, 4096)
- Higher resolution source textures = better quality in combined result
- The addon preserves resolution per UDIM tile (e.g., 4096×4096 source → 4096×4096 combined)

### Complex Materials
- Works with Mix nodes, ColorRamp, and complex node setups
- Preserves all connections and settings
- AO textures mixed with BaseColor are detected and processed separately

### Boundary Cases
- Objects with UVs touching UDIM boundaries (0.0, 1.0) are correctly assigned to their primary tile
- Uses UV bounding box center for accurate tile assignment

## Known Limitations

- **Packed Images**: Images must be either on disk or have valid packed data in Blender
- **Non-Square UDIMs**: Works best with standard 1×1 UDIM tiles
- **Overlapping UVs**: Objects with overlapping UVs in the same UDIM tile may have unexpected results
- **Animated Textures**: Not designed for animated/sequence textures

## Troubleshooting

### "No textures loaded - ABORTING"
- Check that texture files exist on disk
- Ensure textures are properly loaded in Blender (visible in viewport)
- Try reloading textures: `Image Editor > Image > Reload`

### Wrong Colorspace
- The addon attempts to preserve colorspace automatically
- Manually check colorspace settings after combining if needed
- BaseColor should be sRGB, Normal/Roughness/Metallic should be Non-Color

### UV Layout Issues
- Ensure source objects have proper UVs within standard UDIM tile ranges
- Check that UV scale is appropriate (not too large or too small)

### Slow Performance
- First run includes texture packing/loading - subsequent runs are faster
- For 100+ objects, processing may take a few minutes
- Progress is shown in the console

## Development

This addon was developed through an iterative process focusing on:
- Robust texture detection (including non-BSDF connections)
- Fast compositing with PIL
- Proper colorspace management
- Boundary-aware UDIM detection
- Support for both standard UDIM workflows and non-UDIM texture sets

## Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the issues page.

## License

This project is licensed under the GPL-3.0 License - see the LICENSE file for details.

## Acknowledgments

- Built for Blender 4.0+
- Uses PIL (Pillow) for fast image compositing
- Developed with assistance from Claude (Anthropic)

## Support

If you encounter issues:
1. Check the Blender console for detailed error messages
2. Verify texture paths and formats
3. Ensure objects have valid UV maps
4. Check that materials use nodes

For bugs or feature requests, please open an issue on GitHub.

---

**Version**: 1.0.0  
**Blender**: 4.0+  
**Last Updated**: January 2026
