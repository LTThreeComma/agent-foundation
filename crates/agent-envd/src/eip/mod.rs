mod generated {
    #![allow(clippy::all, unused_variables)]

    include!(concat!(env!("OUT_DIR"), "/eip_wire.rs"));
}

pub use generated::*;

#[cfg(test)]
mod tests {
    use serde::{Serialize, de::DeserializeOwned};

    use super::{
        CommandNetwork, EIP_DESCRIPTOR_SHA256, EIP_PROTO_PACKAGE, EIP_PROTOCOL_VERSION, EIPError,
        EIPServerInfo, EipValidate, EnvironmentChangedNotification, FileStatParams, FileStatResult,
        InitializeParams, METHODS, OutputOverflow, OutputPolicy, OutputReadParams,
        ProcessWriteStdinParams, SessionExpiringNotification, ShellExecParams, StateExportParams,
        decode, encode,
    };

    fn assert_golden<T>(value: serde_json::Value)
    where
        T: DeserializeOwned + Serialize + EipValidate,
    {
        let payload = serde_json::to_string(&value).expect("fixture is JSON");
        let decoded: T = decode(&payload).expect("fixture follows EIP profile");
        assert_eq!(
            serde_json::to_value(decoded).expect("model serializes"),
            value
        );
    }

    #[test]
    fn generated_registry_has_complete_v1_surface() {
        assert_eq!(EIP_PROTOCOL_VERSION, "1.0");
        assert_eq!(EIP_PROTO_PACKAGE, "converge.agent_envd.eip.v1");
        assert_eq!(METHODS.len(), 38);
        assert_eq!(
            METHODS
                .iter()
                .filter(|method| method.kind == "request_response")
                .count(),
            34
        );
        assert_eq!(
            METHODS
                .iter()
                .filter(|method| method.kind == "server_notification")
                .count(),
            4
        );
        assert_eq!(EIP_DESCRIPTOR_SHA256.len(), 64);
    }

    #[test]
    fn generated_registry_has_unique_jsonrpc_names() {
        let mut names = METHODS.iter().map(|method| method.name).collect::<Vec<_>>();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), METHODS.len());
    }

    #[test]
    fn rust_models_match_shared_golden_values() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../protocol/eip/v1/testdata/golden.json"))
                .expect("golden fixture is valid JSON");
        for case in fixture["cases"].as_array().expect("cases is an array") {
            let value = case["value"].clone();
            match case["type"].as_str().expect("case type is a string") {
                "InitializeParams" => assert_golden::<InitializeParams>(value),
                "EnvironmentChangedNotification" => {
                    assert_golden::<EnvironmentChangedNotification>(value)
                }
                "FileStatParams" => assert_golden::<FileStatParams>(value),
                "FileStatResult" => assert_golden::<FileStatResult>(value),
                "ShellExecParams" => assert_golden::<ShellExecParams>(value),
                "OutputReadParams" => assert_golden::<OutputReadParams>(value),
                "ProcessWriteStdinParams" => assert_golden::<ProcessWriteStdinParams>(value),
                "StateExportParams" => assert_golden::<StateExportParams>(value),
                "SessionExpiringNotification" => {
                    assert_golden::<SessionExpiringNotification>(value)
                }
                "EIPError" => assert_golden::<EIPError>(value),
                other => panic!("unhandled golden fixture type: {other}"),
            }
        }
    }

    #[test]
    fn rust_models_apply_and_canonically_omit_explicit_defaults() {
        let value = serde_json::json!({
            "context": {"operation_id": "op-defaults"},
            "request": {
                "command": {"kind": "argv", "executable": "true"},
                "cwd": {"mount_id": "workspace", "path": "/repo"},
                "output_policy": {
                    "max_inline_bytes": 1,
                    "max_output_bytes": 1,
                    "overflow": "truncate"
                }
            }
        });
        let payload = serde_json::to_string(&value).expect("fixture is JSON");

        let decoded: ShellExecParams = decode(&payload).expect("fixture follows EIP profile");

        assert_eq!(decoded.request.network, CommandNetwork::Configured);
        assert!(decoded.request.environment.set.is_empty());
        assert!(decoded.request.environment.unset.is_empty());
        assert!(decoded.request.limits.wall_time_ms.is_none());
        assert_eq!(
            serde_json::to_value(decoded).expect("model serializes"),
            value
        );
    }

    #[test]
    fn rust_encoder_rejects_structurally_invalid_models() {
        assert!(
            encode(&EIPServerInfo {
                name: "not-agent-envd".to_owned(),
                version: "1.0.0".to_owned(),
            })
            .is_err()
        );
        assert!(
            encode(&OutputPolicy {
                max_inline_bytes: 2,
                max_output_bytes: 1,
                overflow: OutputOverflow::Truncate,
            })
            .is_err()
        );
    }

    #[test]
    fn rust_decoder_rejects_profile_violations() {
        let unknown = r#"{"context":{"operation_id":"op","principal":"caller"},"path":{"mount_id":"workspace","path":"/repo"}}"#;
        assert!(decode::<FileStatParams>(unknown).is_err());

        let invalid_path = r#"{"context":{"operation_id":"op"},"path":{"mount_id":"workspace","path":"/repo/../secret"}}"#;
        assert!(decode::<FileStatParams>(invalid_path).is_err());

        let missing_position = r#"{"context":{"operation_id":"op"},"reference":"output-1","output_policy":{"max_inline_bytes":1,"max_output_bytes":1,"overflow":"truncate"}}"#;
        assert!(decode::<OutputReadParams>(missing_position).is_err());

        let duplicate_map_key = r#"{"context":{"operation_id":"op"},"request":{"command":{"kind":"argv","executable":"true"},"cwd":{"mount_id":"workspace","path":"/repo"},"environment":{"set":{"PATH":"one","PATH":"two"}},"output_policy":{"max_inline_bytes":1,"max_output_bytes":1,"overflow":"truncate"}}}"#;
        assert!(decode::<ShellExecParams>(duplicate_map_key).is_err());
    }
}
