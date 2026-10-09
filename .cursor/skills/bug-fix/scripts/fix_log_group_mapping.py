#!/usr/bin/env python3
"""Apply CloudWatch-verified naming fixes to log_group_mapping.json."""

import json
from pathlib import Path

PATH = Path(__file__).parent / "log_group_mapping.json"


def replace_in_obj(obj, old: str, new: str):
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str):
                obj[k] = v.replace(old, new)
            else:
                replace_in_obj(v, old, new)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i] = item.replace(old, new)
            else:
                replace_in_obj(item, old, new)


def delete_key(obj, key):
    if isinstance(obj, dict) and key in obj:
        del obj[key]


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))

    data["deployment_notes"] = {
        "prod_ap1": "TC region ap1 (APAC) deploys to ap-southeast-2; infra uses -ap2 / prod-ap2 resource suffix in AWS.",
        "prod_ap2": "TC region ap2 (Tokyo) is not deployed in connect-prod; no CloudWatch log groups (omitted from mapping).",
        "stage_ap1": "TC region ap1 (APAC) uses -AP2 / -ap2 Lambda and ECS suffixes in ap-southeast-2.",
    }

    # Stage AP1: Lambda/CDK suffix is AP2 in AWS
    for section in ("lambdas",):
        for _name, envs in data.get(section, {}).items():
            if "stage" in envs and "ap1" in envs["stage"]:
                envs["stage"]["ap1"] = envs["stage"]["ap1"].replace("-STAGE-AP1", "-STAGE-AP2")

    tcps = data["services"]["tcps-trigger"]["log_groups"]["stage"]["ap1"]["log_groups"]
    data["services"]["tcps-trigger"]["log_groups"]["stage"]["ap1"]["log_groups"] = [
        g.replace("-STAGE-AP1", "-STAGE-AP2") for g in tcps
    ]

    # Stage AP1 ECS / folder export
    data["services"]["file-service"]["log_groups"]["stage"]["ap1"]["log_groups"] = [
        "/ecs/stage-w-files-api-task-ap2"
    ]
    data["services"]["permission-service"]["log_groups"]["stage"]["ap1"]["log_groups"] = [
        "/ecs/stage-permissions-task-ap2"
    ]
    if "FolderExportProcessor" in data["lambdas"]:
        delete_key(data["lambdas"]["FolderExportProcessor"]["stage"], "ap1")

    # Prod AP1: use prod-ap2 / -AP2 names in ap-southeast-2 (no prod-ap1 logs in account)
    replace_in_obj(data["services"]["monolith"]["log_groups"]["prod"]["ap1"], "prod-ap1", "prod-ap2")

    data["services"]["file-service"]["log_groups"]["prod"]["ap1"]["log_groups"] = [
        "/ecs/prod-w-files-api-task-ap2"
    ]
    data["services"]["permission-service"]["log_groups"]["prod"]["ap1"]["log_groups"] = [
        "/ecs/prod-permissions-api-task-ap2"
    ]

    for g in data["services"]["tcps-trigger"]["log_groups"]["prod"]["ap1"]["log_groups"]:
        pass
    data["services"]["tcps-trigger"]["log_groups"]["prod"]["ap1"]["log_groups"] = [
        "/aws/lambda/FileServiceTCPSTriggerLambda-AP2",
        "/aws/lambda/FolderDeleteRevisionLambda-AP2",
    ]

    for lambda_name, envs in data.get("lambdas", {}).items():
        if lambda_name == "FolderExportProcessor":
            if "prod" in envs and "ap1" in envs["prod"]:
                envs["prod"]["ap1"] = "/ecs/folder-export-processor-ap2"
            continue
        if "prod" in envs and "ap1" in envs["prod"]:
            envs["prod"]["ap1"] = envs["prod"]["ap1"].replace("-AP1", "-AP2")

    # Prod AP2 (Tokyo): not deployed — remove log group entries to avoid false negatives
    data["regions_by_environment"]["prod"] = [
        r for r in data["regions_by_environment"]["prod"] if r != "ap2"
    ]

    for service_name, service in data["services"].items():
        log_groups = service.get("log_groups", {})
        for env in log_groups:
            delete_key(log_groups[env], "ap2")

    for lambda_name, envs in data.get("lambdas", {}).items():
        for env in envs:
            delete_key(envs[env], "ap2")

    PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {PATH}")


if __name__ == "__main__":
    main()
