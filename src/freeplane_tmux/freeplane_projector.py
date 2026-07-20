from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .grpc_client import execute_groovy
from .models import Diagnostic

_DIAG_PREFIX = "tmux_mindmap.diag"
_ERROR_ICON = "button_cancel"
_WARNING_ICON = "messagebox_warning"
_INFO_ICON = "dialog_information"


@dataclass(frozen=True)
class ProjectionResult:
    ok: bool
    capabilities: dict[str, Any]
    raw_result: Any | None = None


def capability_probe_script() -> str:
    return """
import groovy.json.JsonOutput

def node = c?.selected ?: node ?: c?.map?.root
if (node == null) {
  return JsonOutput.toJson([ok:false, error:'no-node'])
}

def methods = node.metaClass.methods*.name.toSet()
return JsonOutput.toJson([
  ok: true,
  can_set_status: methods.contains('setStatusInfo') || methods.contains('statusInfo'),
  can_set_attribute: true,
  can_add_icon: methods.contains('addIcon') ||
    methods.contains('addIconAt') || methods.contains('getIcons'),
  can_remove_icon: methods.contains('removeIcon') || methods.contains('getIcons'),
])
""".strip()


def _groovy_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f"'{escaped}'"


def _diagnostics_payload(diagnostics: list[Diagnostic]) -> str:
    return json.dumps([diagnostic.to_json_dict() for diagnostic in diagnostics], ensure_ascii=False)


def _projection_script(diagnostics: list[Diagnostic], *, clear_only: bool) -> str:
    payload_literal = _groovy_string(_diagnostics_payload(diagnostics))
    prefix_literal = _groovy_string(_DIAG_PREFIX)
    error_icon = _groovy_string(_ERROR_ICON)
    warning_icon = _groovy_string(_WARNING_ICON)
    info_icon = _groovy_string(_INFO_ICON)
    clear_flag = "true" if clear_only else "false"
    return f"""
import groovy.json.JsonOutput
import groovy.json.JsonSlurper

def prefix = {prefix_literal}
def clearOnly = {clear_flag}
def diagnostics = new JsonSlurper().parseText({payload_literal})
def root = c?.map?.root
if (root == null) {{
  throw new IllegalStateException('No opened Freeplane map')
}}

def index = [:]
def walk
walk = {{ node ->
  index[node.id.toString()] = node
  node.children.each {{ child -> walk(child) }}
}}
walk(root)

def removeManagedAttrs = {{ node ->
  def names = []
  try {{
    names.addAll(node.attributes.keySet().collect {{ it.toString() }})
  }} catch (Exception ignored) {{}}
  names.findAll {{ it.startsWith(prefix) }}.each {{ name ->
    try {{ node.attributes.remove(name) }} catch (Exception ignored) {{}}
    try {{ node[name] = null }} catch (Exception ignored) {{}}
  }}
}}

def addIconIfPossible = {{ node, iconName ->
  try {{
    if (node.metaClass.respondsTo(node, 'addIcon', String)) {{
      node.addIcon(iconName)
      return true
    }}
  }} catch (Exception ignored) {{}}
  try {{
    def icons = node.icons
    if (icons != null) {{
      def exists = icons.any {{ icon -> icon?.name?.toString() == iconName }}
      if (!exists && node.metaClass.respondsTo(node, 'addIconAt', Integer.TYPE, String)) {{
        node.addIconAt(icons.size(), iconName)
        return true
      }}
    }}
  }} catch (Exception ignored) {{}}
  return false
}}

def removeManagedIcons = {{ node ->
  try {{
    def icons = node.icons
    if (icons == null) return
    def kept = icons.findAll {{ icon ->
      ![{error_icon}, {warning_icon}, {info_icon}].contains(icon?.name?.toString())
    }}
    if (node.metaClass.respondsTo(node, 'setIcons', List)) {{
      node.setIcons(kept)
    }}
  }} catch (Exception ignored) {{}}
}}

def setStatusIfPossible = {{ node, text ->
  try {{ node.statusInfo = text; return true }} catch (Exception ignored) {{}}
  try {{
    if (node.metaClass.respondsTo(node, 'setStatusInfo', String)) {{
      node.setStatusInfo(text)
      return true
    }}
  }} catch (Exception ignored) {{}}
  return false
}}

index.values().each {{ node ->
  removeManagedAttrs(node)
  removeManagedIcons(node)
  setStatusIfPossible(node, '')
}}

if (!clearOnly) {{
  diagnostics.eachWithIndex {{ diagnostic, idx ->
    def node = index[diagnostic.node_id?.toString()]
    if (node == null) return
    def sev = diagnostic.severity?.toString() ?: 'info'
    def code = diagnostic.code?.toString() ?: 'DIAGNOSTIC'
    def message = diagnostic.message?.toString() ?: ''
    try {{ node[prefix + '.' + sev] = code + ': ' + message }} catch (Exception ignored) {{}}
    try {{
      node[prefix + '.json.' + idx] = JsonOutput.toJson(diagnostic)
    }} catch (Exception ignored) {{}}
    setStatusIfPossible(node, '[' + sev.toUpperCase() + '] ' + code + ' ' + message)
    if (sev == 'error') addIconIfPossible(node, {error_icon})
    else if (sev == 'warning') addIconIfPossible(node, {warning_icon})
    else addIconIfPossible(node, {info_icon})
  }}
  def firstError = diagnostics.find {{ (it.severity?.toString() ?: '') == 'error' }}
  if (firstError != null) {{
    def focusNode = index[firstError.node_id?.toString()]
    try {{ if (focusNode != null) c.select(focusNode) }} catch (Exception ignored) {{}}
  }}
}}

return JsonOutput.toJson([
  ok: true,
  cleared: clearOnly,
  diagnostics_count: diagnostics.size(),
])
""".strip()


class FreeplaneDiagnosticProjector:
    def __init__(self, *, address: str, timeout: float):
        self.address = address
        self.timeout = timeout

    def detect_capabilities(self) -> ProjectionResult:
        _raw_text, payload = execute_groovy(
            address=self.address,
            timeout=self.timeout,
            groovy_code=capability_probe_script(),
        )
        capabilities = payload if isinstance(payload, dict) else {"ok": False}
        return ProjectionResult(
            ok=bool(capabilities.get("ok")), capabilities=capabilities, raw_result=payload
        )

    def apply(self, diagnostics: list[Diagnostic]) -> ProjectionResult:
        _raw_text, payload = execute_groovy(
            address=self.address,
            timeout=self.timeout,
            groovy_code=_projection_script(diagnostics, clear_only=False),
        )
        return ProjectionResult(ok=True, capabilities={}, raw_result=payload)

    def clear(self) -> ProjectionResult:
        _raw_text, payload = execute_groovy(
            address=self.address,
            timeout=self.timeout,
            groovy_code=_projection_script([], clear_only=True),
        )
        return ProjectionResult(ok=True, capabilities={}, raw_result=payload)
