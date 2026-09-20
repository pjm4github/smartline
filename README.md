# Enhanced Shape Editor

A PyQt6-based vector graphics editor with polygon editing, bezier curves, boolean operations, grouping, and more.

## Features Overview

- **Multiple Primitive Types**: Ellipse, Rectangle, and Polygon shapes
- **Vertex Editing**: Direct manipulation of polygon vertices with bezier curve support
- **Boolean Operations**: Union, Combine (XOR), Intersect, Subtract, and Fragment
- **Grouping**: Combine multiple shapes into groups with shared transforms
- **Grid & Rotation Snapping**: Precise positioning with Ctrl+drag
- **Sparsify**: Reduce vertex count on complex polygons

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Selection](#selection)
3. [Primitive Types](#primitive-types)
4. [Vertex Editing](#vertex-editing)
5. [Bezier Curves](#bezier-curves)
6. [Rotation](#rotation)
7. [Resizing](#resizing)
8. [Grouping](#grouping)
9. [Boolean Operations](#boolean-operations)
10. [Sparsify](#sparsify)
11. [Grid & Rotation Snapping](#grid--rotation-snapping)
12. [Keyboard Shortcuts](#keyboard-shortcuts)
13. [Context Menu Reference](#context-menu-reference)

---

## Getting Started

### Requirements

- Python 3.8+
- PyQt6

### Installation

```bash
pip install PyQt6
```

### Running

```python
from shape_editor_enhanced_fixed import EnhancedShapeEditorCanvas

# Create the canvas widget
canvas = EnhancedShapeEditorCanvas()
canvas.show()
```

---

## Selection

### Single Selection
- **Left-click** on a shape to select it
- Selected shapes display an orange dashed border
- Selection shows appropriate handles (resize for ellipse/rectangle, vertex for polygon)

### Multi-Selection
- **Ctrl+Click** or **Shift+Click** on shapes to add/remove from selection
- **Rubberband Selection**: Click and drag on empty space to select multiple shapes
- Multi-selected shapes all show orange dashed borders

### Selection Order
- When using boolean operations, the order of selection matters:
  - **A** = First selected shape
  - **B** = Second selected shape
- This affects operations like Subtract (A − B)

---

## Primitive Types

### Ellipse
- Oval/circle shape defined by a bounding rectangle
- Resize using corner handles
- Rotate using the green rotation handle

### Rectangle
- Four-sided shape defined by a bounding rectangle
- Resize using corner handles
- Rotate using the green rotation handle

### Polygon
- Multi-vertex shape with straight or curved edges
- Edit vertices directly by dragging vertex handles
- Add bezier curves for smooth edges
- Supports any number of vertices (minimum 3)

---

## Vertex Editing

When a polygon is selected, vertex handles appear at each corner.

### Vertex Handle Colors
| Color | Meaning |
|-------|---------|
| **Blue** | Corner vertex (sharp angle) |
| **Magenta** | Smooth vertex (bezier curves) |
| **Cyan** | Symmetric vertex (symmetric bezier handles) |
| **Yellow border** | Currently selected vertex |

### Vertex Operations
- **Click** a vertex to select it
- **Drag** a vertex to move it
- **Right-click** a selected vertex to change its type
- **Delete key** removes the selected vertex (minimum 3 vertices)
- **Insert key** or **Ctrl+I** adds a vertex at the midpoint of an edge

### Vertex Types

| Type | Description |
|------|-------------|
| **Corner** | Sharp corner with no bezier handles |
| **Smooth** | Curved corner with independent bezier handles |
| **Symmetric** | Curved corner with mirrored bezier handles |

---

## Bezier Curves

Bezier curves create smooth edges between vertices.

### Creating Curves
1. Select a vertex
2. Right-click and choose **Smooth** or **Symmetric**
3. Bezier control handles appear (small circles connected by dashed lines)

### Editing Curves
- **Drag** the bezier handles to adjust the curve shape
- Handles are shown as small circles with dashed connector lines
- **Smooth** vertices allow independent handle movement
- **Symmetric** vertices mirror handle movements

### Removing Curves
- Right-click a vertex and choose **Corner** to remove bezier handles

---

## Rotation

All shapes can be rotated around their center.

### Using the Rotation Handle
1. Select a shape (the green circular handle appears above it)
2. Drag the green handle in a circular motion
3. The shape rotates around its center

### Rotation Snapping
- Hold **Ctrl** while rotating to snap to angle increments
- Default snap angle: **10°**
- Adjust via right-click menu → **Snap Settings** → **Rotation Snap**

---

## Resizing

Ellipses and rectangles have resize handles at their corners.

### Using Resize Handles
1. Select an ellipse or rectangle
2. Orange handles appear at the four corners
3. Drag a corner to resize the shape
4. The opposite corner stays anchored

---

## Grouping

Combine multiple shapes into a single group for unified transforms.

### Creating a Group
1. Select multiple shapes (Ctrl+Click or rubberband)
2. Right-click and choose **Group**
3. A dashed bounding box appears around the group

### Group Features
- Move all shapes together by dragging the group
- Resize all shapes proportionally using group corner handles
- Rotate all shapes around the group center
- Group bounding box rotates with the content

### Ungrouping
1. Select a group (click any member shape or the bounding box)
2. Right-click and choose **Ungroup**
3. Shapes return to individual selection

---

## Boolean Operations

Combine two shapes using boolean operations. Available when exactly 2 shapes are selected.

### Operations

| Operation | Symbol | Description |
|-----------|--------|-------------|
| **Union** | A ∪ B | Combines both shapes into one |
| **Combine (XOR)** | A ⊕ B | Union minus the intersection (excludes overlap) |
| **Intersect** | A ∩ B | Only the overlapping area |
| **Subtract** | A − B | First shape minus the second shape |
| **Fragment** | — | Splits into 3 parts: A only, intersection, B only |

### Using Boolean Operations
1. Select exactly 2 shapes (selection order matters for Subtract)
2. Right-click to open context menu
3. Choose the desired operation from **Boolean Operations** section

### Notes
- Results are converted to polygon primitives
- Original shapes are deleted
- Fragment creates multiple independent shapes with different colors
- Complex results may have many vertices (use Sparsify to simplify)

---

## Sparsify

Reduce the vertex count of complex polygons while preserving shape.

### When to Use
- After boolean operations that create many vertices
- To simplify imported or generated polygons
- To reduce file size and improve performance

### Using Sparsify
1. Select a single polygon with more than 4 vertices
2. Right-click (with no vertex selected) and choose **Sparsify**
3. Adjust the **tolerance** value in the dialog:
   - Higher tolerance = more vertices removed
   - Lower tolerance = more detail preserved
4. Click OK to apply

### Dialog Information
- Shows current vertex count
- Displays edge length statistics (min, avg, max)
- Shows vertex deviation statistics (how much each vertex contributes to the shape)
- Default tolerance is set to remove ~25% of vertices per pass

### Algorithm
Uses perpendicular distance measurement:
- If removing a vertex causes less than the tolerance deviation from a straight line between its neighbors, it's removed
- Corners and important shape features are preserved
- Multiple passes are applied until no more vertices can be removed

---

## Grid & Rotation Snapping

Precise positioning using snap-to-grid functionality.

### Grid Snapping
- Hold **Ctrl** while dragging a shape to snap to grid
- Default grid size: **20 pixels**
- Works for both individual shapes and groups

### Rotation Snapping
- Hold **Ctrl** while rotating to snap to angle increments
- Default snap angle: **10 degrees**
- Works for both individual shapes and groups

### Adjusting Snap Settings
1. Right-click anywhere on the canvas
2. Go to **Snap Settings (Ctrl+drag)** submenu
3. Click **Grid Snap** to change grid size (1-500 pixels)
4. Click **Rotation Snap** to change angle increment (1-90 degrees)

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Delete** | Delete selected vertex |
| **Insert** | Add vertex at edge midpoint |
| **Ctrl+I** | Add vertex at edge midpoint |
| **Ctrl+Click** | Add/remove from multi-selection |
| **Shift+Click** | Add/remove from multi-selection |
| **Ctrl+Drag** | Snap to grid while moving |
| **Ctrl+Rotate** | Snap to angle while rotating |

---

## Context Menu Reference

Right-click to access context-sensitive options:

### When Vertex Selected
- **Vertex Type**
  - Corner (sharp)
  - Smooth (bezier)
  - Symmetric (mirrored bezier)

### When Single Polygon Selected (no vertex)
- **Sparsify** - Reduce vertex count

### When 2 Shapes Selected
- **Boolean Operations**
  - Union (A ∪ B)
  - Combine/XOR (A ⊕ B)
  - Intersect (A ∩ B)
  - Subtract (A − B)
  - Fragment

### When Multiple Shapes Selected
- **Group** - Combine into group

### When Group Selected
- **Ungroup** - Dissolve group

### Always Available
- **Snap Settings**
  - Grid Snap size
  - Rotation Snap angle

---

## Coordinate System

The editor uses a local coordinate system for each shape:

- **Position**: Shapes are positioned at their center point
- **Path**: Shape geometry is defined relative to the center
- **Rotation**: Applied as a Qt transform around the center
- **Handles**: Are children of their parent shape, inheriting transforms

This architecture ensures:
- Rotation works correctly around shape centers
- Child handles move and rotate with their parent
- Coordinate transforms are consistent across operations

---

## Signals

The canvas emits signals for integration with other components:

| Signal | Arguments | Description |
|--------|-----------|-------------|
| `primitive_selected` | `prim_id: str` | Shape was selected |
| `shape_modified` | — | Any shape data changed |

---

## License

MIT License

---

## Contributing

Contributions welcome! Please ensure:
- Code follows existing style conventions
- New features include appropriate debug output
- Complex operations handle edge cases gracefully
