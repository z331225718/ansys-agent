from __future__ import annotations

from typing import Any, Callable

from aedt_agent.operator_api import OperatorContractError, OperatorSchemaCatalog, OperatorService, READ_ONLY_OPERATIONS
from aedt_agent.operator_bridge.protocol import response_error, response_ok


class BridgeDispatcher:
    def __init__(self, service: OperatorService, catalog: OperatorSchemaCatalog):
        self.service = service
        self.catalog = catalog
        self.handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "system.handshake": service.handshake, "template.catalog": service.template_catalog,
            "system.preflight": service.system_preflight, "graph.status": service.graph_status,
            "replay.delta": service.replay_delta, "graph.visualize": service.graph_visualize,
            "artifact.query_bounded": service.artifact_query_bounded, "approval.get_pending": service.approval_get_pending,
            "approval.prepare_decision": service.approval_prepare_decision,
        }

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            self.catalog.validate_request(request)
            operation = str(request["operation"])
            if operation not in READ_ONLY_OPERATIONS:
                return response_error(request, "unsupported_operation", "operation is disabled in read-only shadow mode")
            handler = self.handlers[operation]
            # Handshake identity is transport metadata; it does not alter capabilities.
            result = handler() if operation == "system.handshake" else handler(**request["arguments"])
            if "data" not in result:
                result = {"data": result}
            self.catalog.validate_success_data(operation, result.get("data"))
            response = response_ok(request, result)
            self.catalog.validate_terminal_response(response)
            return response
        except OperatorContractError as error:
            code = error.code if error.code in {"invalid_argument", "invalid_cursor", "unsupported_operation"} else "internal"
            return response_error(request, code, str(error))
        except KeyError as error:
            return response_error(request, "not_found", str(error))
        except Exception:
            return response_error(request, "internal", "read-only bridge could not complete the observation")
