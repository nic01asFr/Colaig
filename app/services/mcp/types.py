"""
Types de données MCP (Model Context Protocol) — spec 2025-03-26.

Couvre tous les primitives échangées entre client et serveur :
Tools, Resources, Prompts, Roots, Content blocks, résultats.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ─── Content blocks ──────────────────────────────────────────────────────────

class ContentType(str, Enum):
    TEXT     = "text"
    IMAGE    = "image"
    RESOURCE = "resource"


@dataclass
class MCPContent:
    """Un bloc de contenu retourné par un outil, une ressource ou un prompt."""
    type: ContentType
    text: Optional[str] = None          # pour type=text
    data: Optional[str] = None          # pour type=image (base64)
    mime_type: Optional[str] = None     # pour type=image
    resource_uri: Optional[str] = None  # pour type=resource
    resource_text: Optional[str] = None
    resource_blob: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPContent":
        t = d.get("type", "text")
        if t == "text":
            return cls(type=ContentType.TEXT, text=d.get("text", ""))
        elif t == "image":
            return cls(
                type=ContentType.IMAGE,
                data=d.get("data", ""),
                mime_type=d.get("mimeType"),
            )
        elif t == "resource":
            res = d.get("resource", {})
            return cls(
                type=ContentType.RESOURCE,
                resource_uri=res.get("uri"),
                mime_type=res.get("mimeType"),
                resource_text=res.get("text"),
                resource_blob=res.get("blob"),
            )
        return cls(type=ContentType.TEXT, text=str(d))

    def as_text(self) -> str:
        """Représentation textuelle consolidée pour injection dans le LLM."""
        if self.type == ContentType.TEXT:
            return self.text or ""
        elif self.type == ContentType.IMAGE:
            return f"[image/{self.mime_type or 'unknown'}]"
        elif self.type == ContentType.RESOURCE:
            if self.resource_text:
                return self.resource_text
            if self.resource_blob:
                return f"[resource binaire: {self.resource_uri}]"
            return f"[resource: {self.resource_uri}]"
        return ""


# ─── Capacités serveur (négociées lors de initialize) ────────────────────────

@dataclass
class ServerCapabilities:
    has_tools: bool = False
    tools_list_changed: bool = False
    has_resources: bool = False
    resources_subscribe: bool = False
    resources_list_changed: bool = False
    has_prompts: bool = False
    prompts_list_changed: bool = False
    has_completions: bool = False
    has_logging: bool = False
    protocol_version: str = ""
    server_name: str = ""
    server_version: str = ""
    instructions: str = ""

    @classmethod
    def from_init_result(cls, result: Dict[str, Any]) -> "ServerCapabilities":
        caps = result.get("capabilities", {})
        tools_cap = caps.get("tools", None)
        res_cap = caps.get("resources", None)
        prompts_cap = caps.get("prompts", None)
        info = result.get("serverInfo", {})
        return cls(
            has_tools=tools_cap is not None,
            tools_list_changed=bool((tools_cap or {}).get("listChanged")),
            has_resources=res_cap is not None,
            resources_subscribe=bool((res_cap or {}).get("subscribe")),
            resources_list_changed=bool((res_cap or {}).get("listChanged")),
            has_prompts=prompts_cap is not None,
            prompts_list_changed=bool((prompts_cap or {}).get("listChanged")),
            has_completions="completions" in caps,
            has_logging="logging" in caps,
            protocol_version=result.get("protocolVersion", ""),
            server_name=info.get("name", ""),
            server_version=info.get("version", ""),
            instructions=result.get("instructions", ""),
        )


# ─── Tools ───────────────────────────────────────────────────────────────────

@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    server_name: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any], server_name: str = "") -> "MCPTool":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            input_schema=d.get("inputSchema", {}),
            server_name=server_name,
        )

    @property
    def qualified_name(self) -> str:
        return f"{self.server_name}__{self.name}" if self.server_name else self.name

    def to_llm_description(self) -> Dict[str, Any]:
        """Format pour injection dans un message système LLM."""
        return {
            "name": self.qualified_name,
            "description": self.description,
            "parameters": self.input_schema,
        }


@dataclass
class MCPToolResult:
    tool_name: str
    contents: List[MCPContent] = field(default_factory=list)
    is_error: bool = False

    @property
    def text(self) -> str:
        return "\n".join(c.as_text() for c in self.contents)

    @classmethod
    def from_dict(cls, tool_name: str, d: Dict[str, Any]) -> "MCPToolResult":
        return cls(
            tool_name=tool_name,
            contents=[MCPContent.from_dict(c) for c in d.get("content", [])],
            is_error=bool(d.get("isError", False)),
        )


# ─── Resources ───────────────────────────────────────────────────────────────

@dataclass
class MCPResource:
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""
    size: Optional[int] = None
    server_name: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any], server_name: str = "") -> "MCPResource":
        return cls(
            uri=d["uri"],
            name=d.get("name", ""),
            description=d.get("description", ""),
            mime_type=d.get("mimeType", ""),
            size=d.get("size"),
            server_name=server_name,
        )


@dataclass
class MCPResourceContent:
    uri: str
    contents: List[MCPContent] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(c.as_text() for c in self.contents)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPResourceContent":
        raw_contents = d.get("contents", [])
        contents = []
        for item in raw_contents:
            if "text" in item:
                contents.append(MCPContent(
                    type=ContentType.TEXT,
                    text=item["text"],
                    mime_type=item.get("mimeType"),
                ))
            elif "blob" in item:
                contents.append(MCPContent(
                    type=ContentType.IMAGE,
                    data=item["blob"],
                    mime_type=item.get("mimeType"),
                ))
        return cls(uri=d.get("uri", ""), contents=contents)


# ─── Prompts ─────────────────────────────────────────────────────────────────

@dataclass
class MCPPromptArgument:
    name: str
    description: str = ""
    required: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPPromptArgument":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            required=bool(d.get("required", False)),
        )


@dataclass
class MCPPrompt:
    name: str
    description: str = ""
    arguments: List[MCPPromptArgument] = field(default_factory=list)
    server_name: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any], server_name: str = "") -> "MCPPrompt":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            arguments=[MCPPromptArgument.from_dict(a) for a in d.get("arguments", [])],
            server_name=server_name,
        )


@dataclass
class MCPPromptMessage:
    role: str  # "user" | "assistant"
    content: MCPContent

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPPromptMessage":
        return cls(
            role=d.get("role", "user"),
            content=MCPContent.from_dict(d.get("content", {"type": "text", "text": ""})),
        )

    def to_llm_message(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content.as_text()}


@dataclass
class MCPPromptResult:
    description: str = ""
    messages: List[MCPPromptMessage] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPPromptResult":
        return cls(
            description=d.get("description", ""),
            messages=[MCPPromptMessage.from_dict(m) for m in d.get("messages", [])],
        )

    def to_llm_messages(self) -> List[Dict[str, Any]]:
        return [m.to_llm_message() for m in self.messages]


# ─── Roots ────────────────────────────────────────────────────────────────────

@dataclass
class MCPRoot:
    """Racine de workspace exposée par le client au serveur."""
    uri: str           # file:// ou webdav:// URI
    name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"uri": self.uri}
        if self.name:
            d["name"] = self.name
        return d


# ─── Completion ───────────────────────────────────────────────────────────────

@dataclass
class MCPCompletionResult:
    values: List[str] = field(default_factory=list)
    total: Optional[int] = None
    has_more: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPCompletionResult":
        c = d.get("completion", {})
        return cls(
            values=c.get("values", []),
            total=c.get("total"),
            has_more=bool(c.get("hasMore", False)),
        )
