"""HTML version of a notification, for email.

Email clients ignore stylesheets and most modern CSS, so this is a table
layout with inline styles and explicit colours.
"""
from __future__ import annotations

from html import escape

# tone -> (accent colour, label shown above the headline)
TONES = {
    "action": ("#1a73e8", "Action needed"),
    "ok": ("#188038", "All good"),
    "info": ("#5f6368", "For your information"),
    "problem": ("#c5221f", "Something went wrong"),
}
FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def render(
    title: str,
    message: str,
    *,
    tone: str = "problem",
    facts: list[tuple[str, str]] | None = None,
    links: list[tuple[str, str]] | None = None,
) -> str:
    """Return a complete HTML document. The first link becomes the button."""
    accent, label = TONES.get(tone, TONES["problem"])
    headline = title.removeprefix("ScaleEV: ")
    headline = headline[:1].upper() + headline[1:]
    rows = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;color:#5f6368;font-size:14px;white-space:nowrap;vertical-align:top;width:110px">{escape(name)}</td>'
        f'<td style="padding:6px 0;color:#202124;font-size:14px;font-weight:600">{escape(value)}</td></tr>'
        for name, value in facts or []
    )
    button = extra = ""
    if links:
        text, url = links[0]
        button = (
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0 4px"><tr>'
            f'<td bgcolor="{accent}" style="border-radius:8px">'
            f'<a href="{escape(url, quote=True)}" style="display:inline-block;padding:13px 28px;color:#ffffff;'
            f'font-size:16px;font-weight:600;text-decoration:none;{FONT}">{escape(text)}</a></td></tr></table>'
        )
        extra = "".join(
            f'<p style="margin:14px 0 0;font-size:13px;color:#5f6368">{escape(text)}: '
            f'<a href="{escape(url, quote=True)}" style="color:{accent}">open link</a></p>'
            for text, url in links[1:]
        )
    facts_table = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 0;border-top:1px solid #e8eaed;'
        f'padding-top:10px;width:100%">{rows}</table>'
        if rows
        else ""
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:#f1f3f4;{FONT}">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f1f3f4"><tr>'
        '<td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff" '
        'style="max-width:480px;border-radius:12px;overflow:hidden">'
        f'<tr><td bgcolor="{accent}" style="height:6px;font-size:0;line-height:0">&nbsp;</td></tr>'
        '<tr><td style="padding:28px 28px 30px">'
        f'<p style="margin:0 0 6px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{accent};'
        f'font-weight:700">{escape(label)}</p>'
        f'<h1 style="margin:0 0 12px;font-size:22px;line-height:1.3;color:#202124">{escape(headline)}</h1>'
        f'<p style="margin:0;font-size:16px;line-height:1.5;color:#3c4043">{escape(message)}</p>'
        f"{facts_table}{button}{extra}"
        "</td></tr></table>"
        '<p style="margin:16px 0 0;font-size:12px;color:#80868b">Sent by the ScaleEV integration in Home Assistant</p>'
        "</td></tr></table></body></html>"
    )
