from typing import Any


class PatchValidator:
    REQUIRED_FIELDS = (
        "schema",
        "target_files",
        "change_type",
        "reason",
        "current_rule_read",
        "critique",
        "minimal_patch",
        "verification",
        "approval_required",
        "operator_approval",
    )

    def validate(self, plan: dict[str, Any]) -> list[str]:
        errors: list[str] = []

        if not isinstance(plan, dict):
            return ["plan must be a dict"]

        for field in self.REQUIRED_FIELDS:
            if field not in plan:
                errors.append(f"missing required field: {field}")

        if "schema" in plan and plan["schema"] != "skeleton.patch_plan.v1":
            errors.append("schema must equal skeleton.patch_plan.v1")

        if "target_files" in plan:
            target_files = plan["target_files"]
            if not isinstance(target_files, list) or not target_files:
                errors.append("target_files must be a non-empty list of strings")
            elif not all(isinstance(item, str) for item in target_files):
                errors.append("target_files must be a non-empty list of strings")

        if "change_type" in plan:
            change_type = plan["change_type"]
            if not isinstance(change_type, str) or not change_type.strip():
                errors.append("change_type must be a non-empty string")

        if "reason" in plan:
            reason = plan["reason"]
            if not isinstance(reason, str) or not reason.strip():
                errors.append("reason must be a non-empty string")

        if "current_rule_read" in plan and not isinstance(plan["current_rule_read"], bool):
            errors.append("current_rule_read must be a boolean")

        if "critique" in plan and not isinstance(plan["critique"], str):
            errors.append("critique must be a string")

        if "minimal_patch" in plan and not isinstance(plan["minimal_patch"], str):
            errors.append("minimal_patch must be a string")

        if "verification" in plan:
            verification = plan["verification"]
            if not isinstance(verification, list) or not verification:
                errors.append("verification must be a non-empty list of strings")
            elif not all(isinstance(item, str) for item in verification):
                errors.append("verification must be a non-empty list of strings")

        if "approval_required" in plan and not isinstance(plan["approval_required"], bool):
            errors.append("approval_required must be a boolean")

        if "operator_approval" in plan and not isinstance(plan["operator_approval"], bool):
            errors.append("operator_approval must be a boolean")

        return errors
