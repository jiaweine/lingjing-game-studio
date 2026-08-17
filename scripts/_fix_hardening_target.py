from pathlib import Path

path = Path(__file__).with_name("_harden_product_completion.py")
text = path.read_text(encoding="utf-8")
old = '''replace_once(
    "frontend/app.js",
    \'\'\'      workspace_name: $("registerWorkspace").value.trim(),\\n    };\\n\'\'\',
    \'\'\'      workspace_name: $("registerWorkspace").value.trim(),\\n      invite_token: new URLSearchParams(location.search).get("invite") || null,\\n    };\\n\'\'\',
)
'''
new = '''replace_once(
    "frontend/app.js",
    \'\'\'          workspace_name: $("registerWorkspace").value.trim(),\\n          email: $("registerEmail").value.trim(),\\n\'\'\',
    \'\'\'          workspace_name: $("registerWorkspace").value.trim(),\\n          invite_token: new URLSearchParams(location.search).get("invite") || null,\\n          email: $("registerEmail").value.trim(),\\n\'\'\',
)
'''
if old not in text:
    raise RuntimeError("registration hardening block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
