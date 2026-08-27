"""Turning a component's option schema into a form.

The schema is the single source: a component that grows an option grows a field
here with no edit, and -- the point of doing it this way -- a component cannot
grow an option the dashboard silently cannot set.
"""

from __future__ import annotations

import zoneinfo

from .layout import e


def _timezones() -> list[str]:
    """Every IANA zone this machine knows, Europe first.

    A free-text box demanded you already knew that Madrid is `Europe/Madrid`.
    Paired with an <input list=...> the browser filters as you type, which is
    the behaviour asked for and costs no script.
    """
    try:
        names = sorted(zoneinfo.available_timezones())
    except Exception:                    # pragma: no cover - platform tzdata
        return ["UTC"]
    europe = [n for n in names if n.startswith("Europe/")]
    return europe + [n for n in names if not n.startswith("Europe/")]


#: Named lists a schema may point at, rather than inlining hundreds of choices
#: into every component that wants one.
DATALISTS = {"timezones": _timezones}


def datalist_markup(schema) -> str:
    """The <datalist> elements the fields in `schema` refer to, once each."""
    wanted = {f.get("datalist") for f in schema or ()} & set(DATALISTS)
    out = []
    for name in sorted(wanted):
        opts = "".join(f'<option value="{e(v)}">' for v in DATALISTS[name]())
        out.append(f'<datalist id="dl-{e(name)}">{opts}</datalist>')
    return "".join(out)


def field(spec: dict, value) -> str:
    """One labelled control for one option."""
    key = spec.get("key")
    label = spec.get("label", key)
    kind = spec.get("type", "text")
    hint = (f'<span class="hint">{e(spec["help"])}</span>'
            if spec.get("help") else "")
    name = f"opt.{e(key)}"

    if kind == "bool":
        checked = " checked" if value else ""
        return (f'<label class="check"><input type="checkbox" name="{name}" '
                f'value="1"{checked}> {e(label)}{hint}</label>')

    if kind == "choice":
        picks = "".join(
            f'<option value="{e(o)}"{" selected" if o == value else ""}>'
            f'{e(o)}</option>' for o in spec.get("choices", ()))
        return (f'<label class="field">{e(label)}'
                f'<select name="{name}">{picks}</select>{hint}</label>')

    listing = (f' list="dl-{e(spec["datalist"])}"'
               if spec.get("datalist") in DATALISTS else "")
    itype = "number" if kind == "int" else "text"
    shown = "" if value is None else str(value)
    placeholder = ("" if spec.get("placeholder") is None
                   else f' placeholder="{e(spec["placeholder"])}"')
    return (f'<label class="field">{e(label)}'
            f'<input type="{itype}" name="{name}" value="{e(shown)}"'
            f'{listing}{placeholder}>{hint}</label>')


def option_group(scene: str, schema, values, *, active: bool) -> str:
    """One component's whole config block.

    Inactive groups are `disabled`, not merely hidden: a hidden input still
    posts its value, so without this, picking a clock would submit the radar's
    options alongside it.
    """
    if not schema:
        body = ('<p class="empty">Este componente no tiene ajustes.</p>')
    else:
        body = "".join(field(f, (values or {}).get(f.get("key"), f.get("default")))
                       for f in schema)
    state = "" if active else " hidden disabled"
    return (f'<fieldset class="optgroup" data-scene="{e(scene)}"{state}'
            f' style="border:0;padding:0;margin:0">'
            f'<div class="stack">{body}</div></fieldset>')
