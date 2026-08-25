from __future__ import annotations

import converge_agent_harness as harness
import converge_agent_harness.capabilities as capabilities
import converge_agent_harness.environment as environment
import converge_agent_harness.filters as filters
import converge_agent_harness.tools as tools
import converge_agent_harness.toolsets as toolsets


def test_block_five_root_facade_exports_documented_capabilities_and_toolsets() -> None:
    expected = {
        "CompactionCapability",
        "DocumentsCapability",
        "DocumentsRunCapability",
        "DocumentsToolset",
        "DynamicEnvironmentCapability",
        "FileContextCapability",
        "FileToolset",
        "HandoffCapability",
        "HandoffToolset",
        "MediaCapability",
        "MediaRunCapability",
        "MediaToolset",
        "ModelCostRunCapability",
        "MonitoredProcessCapability",
        "MonitoredProcessRunCapability",
        "MonitoredProcessToolset",
        "RunUsageLedger",
        "RuntimeContextCapability",
        "ShellToolset",
        "SkillsCapability",
        "TaskStateRunCapability",
        "UserInteractionCapability",
        "UserInteractionToolset",
        "WebCapability",
        "WebRunCapability",
        "WebToolset",
        "WorkingStateCapability",
        "WorkingStateToolset",
    }

    assert expected <= set(harness.__all__)
    assert all(hasattr(harness, name) for name in expected)


def test_block_five_feature_facades_export_documented_families() -> None:
    expected_capabilities = {
        "CompactionCapability",
        "DocumentsCapability",
        "DocumentsRunCapability",
        "FileContextCapability",
        "HandoffCapability",
        "MediaCapability",
        "MediaRunCapability",
        "MonitoredProcessCapability",
        "MonitoredProcessRunCapability",
        "RuntimeContextCapability",
        "SkillsCapability",
        "TaskStateRunCapability",
        "UserInteractionCapability",
        "WebCapability",
        "WebRunCapability",
        "WorkingStateCapability",
    }
    expected_toolsets = {
        "DocumentsToolset",
        "FileToolset",
        "HandoffToolset",
        "MediaToolset",
        "MonitoredProcessToolset",
        "ShellToolset",
        "UserInteractionToolset",
        "WebToolset",
        "WorkingStateToolset",
        "DEFAULT_TOOL_OUTPUT_CHARS",
        "FINAL_TOOL_OUTPUT_HARD_CHARS",
        "MAX_TOOL_OUTPUT_SPILL_BYTES",
        "ToolOutputDisclosure",
        "acknowledge_tool_output",
        "continuation_disclosure",
        "create_tool_output_disclosure",
        "disclose_mapping_field",
        "disclose_sequence_field",
        "disclose_text_fields",
        "disclose_text_paths",
        "fit_text_fields_to_limit",
        "tool_output_bytes",
        "tool_output_size",
        "tool_output_text",
    }
    expected_filters = {
        "ColdStartFilterCapability",
        "ColdStartFilterConfiguration",
        "ContentFilterCapability",
        "ContentFilterConfiguration",
        "MediaFamily",
        "MessageIntegrityFilterCapability",
    }

    assert expected_capabilities <= set(capabilities.__all__)
    assert expected_toolsets == set(toolsets.__all__)
    assert expected_filters == set(filters.__all__)


def test_block_five_environment_and_managed_tool_import_routes_are_public() -> None:
    expected_environment = {
        "DirectLocalEnvironmentConfiguration",
        "DirectLocalEnvironmentProviderBinding",
        "DirectLocalFilePolicy",
        "DirectLocalOutputPolicy",
        "DirectLocalPortPolicy",
        "DirectLocalProcessPolicy",
        "DirectLocalRootConfiguration",
        "DirectLocalShellProfile",
        "DynamicEnvironmentCapability",
        "EnvironmentRunBinding",
        "create_environment_run_binding",
    }
    expected_tools = {
        "ClientToolDefinition",
        "ClientToolsCapability",
        "ClientToolsRunCapability",
        "ClientToolsSpec",
        "ClientToolsetDefinition",
        "InvocationPolicyCapability",
        "InvocationPolicyDecision",
    }

    assert expected_environment <= set(environment.__all__)
    assert expected_tools <= set(tools.__all__)
    assert "InvocationAuthorizationCapability" not in tools.__all__
    assert "InvocationAuthorizationToolset" not in tools.__all__
