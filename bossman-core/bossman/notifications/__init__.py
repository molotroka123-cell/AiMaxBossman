__all__=["router","build_subsystem","STORE","TELEGRAM","BRIDGE","DISPATCHER"]
def __getattr__(name):
    if name=="router":
        from .routes import router
        return router
    if name=="build_subsystem":
        from .subsystem import build_subsystem
        return build_subsystem
    if name in {"STORE","TELEGRAM","BRIDGE","DISPATCHER"}:
        from .runtime import STORE,TELEGRAM,BRIDGE,DISPATCHER
        return {"STORE":STORE,"TELEGRAM":TELEGRAM,"BRIDGE":BRIDGE,"DISPATCHER":DISPATCHER}[name]
    raise AttributeError(name)
