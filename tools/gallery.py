"""Render every registered default mode to docs/gallery.png (matplotlib, no Qt)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import matplotlib                                           # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                             # noqa: E402
from matplotlib.patches import Rectangle                    # noqa: E402

from smartline import RouteContext, default_routers, geometry as g   # noqa: E402

OBS = [(140, 90, 300, 180), (420, 60, 540, 260), (640, 140, 840, 220),
       (230, 330, 410, 450), (520, 380, 660, 520), (760, 330, 880, 550)]
WIRE = [(40, 300), (470, 300), (470, 585), (960, 585)]      # existing net (bus guide)
A, B = (60, 60), (930, 590)
fig, axes = plt.subplots(2, 4, figsize=(20, 8.2))
for ax, r in zip(axes.flat, default_routers()):
    a = (60, 312) if r.name == "bus" else A
    for flip, style in ((False, dict(color="#1f6fd0", lw=2.2)), (True, dict(color="#d0571f", lw=1.4, ls="--"))):
        ctx = RouteContext(obstacles=OBS, clearance=10, flip=flip, guides=[WIRE])
        pts = r.shape(a, B, ctx).flatten()
        ax.plot(*zip(*pts), **style, label=("posture flipped" if flip else "default") +
                f"  ({g.length(pts):.0f}px)")
    ax.plot(*zip(*WIRE), color="#f0a000", lw=4, alpha=.45)
    for l, t, rt, b in OBS:
        ax.add_patch(Rectangle((l, t), rt - l, b - t, fc="#e1e8f5", ec="#465a82"))
        ax.add_patch(Rectangle((l - 10, t - 10), rt - l + 20, b - t + 20, fill=False, ec="#9aa7c0", ls=":", lw=.8))
    ax.plot(*a, "o", color="k"); ax.plot(*B, "s", color="k")
    ax.set_title(f"{r.label}   [{r.name}]"); ax.set_xlim(0, 1000); ax.set_ylim(620, 0)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.legend(fontsize=7, loc="upper right")
fig.tight_layout()
fig.savefig(os.path.join(os.path.dirname(__file__), "..", "docs", "gallery.png"), dpi=100)
