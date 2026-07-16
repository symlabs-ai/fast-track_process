"""Template discovery, materialization, and run-input contracts.

The public execution vocabulary is deliberately template-only.  A template
is distributed by the engine, materialized once into the project-owned
``.ft/process/<template>/`` directory, and then preserved as a local fork.
"""

from ft.templates.catalog import (
    InitTemplateDescriptor,
    ResolvedTemplate,
    TemplateCatalog,
    TemplateCatalogError,
    TemplateDescriptor,
    TemplateNotFoundError,
    template_kind,
)
from ft.templates.input_policy import (
    InputPolicy,
    InputPolicyError,
    InputRequiredError,
    PreparedInput,
    load_input_policy,
)
from ft.templates.materialize import TemplateMaterializer, resolve_template

__all__ = [
    "InitTemplateDescriptor",
    "InputPolicy",
    "InputPolicyError",
    "InputRequiredError",
    "PreparedInput",
    "ResolvedTemplate",
    "TemplateCatalog",
    "TemplateCatalogError",
    "TemplateDescriptor",
    "TemplateMaterializer",
    "TemplateNotFoundError",
    "load_input_policy",
    "resolve_template",
    "template_kind",
]
