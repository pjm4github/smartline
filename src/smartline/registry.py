"""Router registry - how applications add, replace and order routing modes.

    from smartline import register, Router

    @register                      # name comes from the class attribute
    class MyRouter(Router):
        name, label = "mine", "My router"
        def route(self, start, end, ctx): ...

    @register(replace=True)        # take over a built-in mode
    class PictoCubic(CubicRouter):
        def smooth(self, skeleton, ctx): ...

    register("hug45", lambda: HugRouter(OctilinearRouter()))   # factory form

    tool = SmartLineTool(scene, modes=["ortho", "hug", "bus", "cubic", "mine"])

Installed packages can also contribute modes without being imported first, via
the ``smartline.routers`` entry-point group::

    [project.entry-points."smartline.routers"]
    picto-cubic = "pictosync.routing:PictoCubic"
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Union

from . import routers as R

Factory = Callable[[], R.Router]
_REGISTRY: Dict[str, Factory] = {}
_plugins_loaded = False

#: order of the mode ring a tool gets when it is not told otherwise
DEFAULT_MODES: List[str] = ["straight", "ortho", "hug", "hug-straight",
                            "avoid", "octilinear", "bus", "cubic"]


def register(name_or_factory: Union[str, Factory, None] = None,
             factory: Optional[Factory] = None, *, replace: bool = False):
    """Register a router class or zero-argument factory.

    Usable as ``@register``, ``@register("name")``, ``@register(replace=True)``
    or ``register("name", factory)``.  Registering an existing name raises
    unless ``replace=True`` - overriding a built-in is always explicit.
    """
    def add(name: Optional[str], fac: Factory) -> Factory:
        key = name or getattr(fac, "name", None)
        if not key or key == "base":
            raise ValueError("router needs a name (class attribute or register('name', ...))")
        if key in _REGISTRY and not replace:
            raise KeyError(f"router {key!r} already registered; pass replace=True to override")
        _REGISTRY[key] = fac
        return fac

    if callable(name_or_factory):                       # @register
        return add(None, name_or_factory)
    if factory is not None:                             # register("n", factory)
        return add(name_or_factory, factory)
    return lambda fac: add(name_or_factory, fac)        # @register("n") / (replace=True)


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def available() -> List[str]:
    load_plugins()
    return list(_REGISTRY)


def create(name: str) -> R.Router:
    load_plugins()
    try:
        router = _REGISTRY[name]()
    except KeyError:
        raise KeyError(f"unknown router {name!r}; available: {', '.join(_REGISTRY)}") from None
    if router.name != name:                # the registry key is the mode's identity
        router.name = name
    return router


def default_routers(modes: Optional[Iterable[Union[str, R.Router]]] = None) -> List[R.Router]:
    """Instantiate a mode ring.  Items may be registry names or Router instances."""
    return [m if isinstance(m, R.Router) else create(m) for m in (modes or DEFAULT_MODES)]


def load_plugins(force: bool = False) -> None:
    """Import routers advertised under the ``smartline.routers`` entry point."""
    global _plugins_loaded
    if _plugins_loaded and not force:
        return
    _plugins_loaded = True
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="smartline.routers") if hasattr(eps, "select") \
            else eps.get("smartline.routers", [])
    except Exception:                                   # pragma: no cover
        return
    for ep in group:
        try:
            obj = ep.load()
            _REGISTRY[ep.name] = obj                    # plugins may override built-ins
        except Exception as exc:                        # pragma: no cover
            import warnings
            warnings.warn(f"smartline: could not load router plugin {ep.name!r}: {exc}")


# ---- built-ins ---------------------------------------------------------------
register(R.StraightRouter)
register(R.OrthoRouter)
register("ortho-alternate", lambda: R.OrthoRouter("alternate"))
register("hug", lambda: R.HugRouter(R.OrthoRouter(), name="hug", label="Hug (orthogonal)"))
register("hug-straight", lambda: R.HugRouter(R.StraightRouter(), name="hug-straight", label="Hug (straight)"))
register(R.AvoidRouter)
register(R.OctilinearRouter)
register(R.BusRouter)
register(R.CubicRouter)
register("cubic-hug", lambda: R.CubicRouter(R.HugRouter(R.OrthoRouter())))
