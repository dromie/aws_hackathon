#!/usr/bin/env python3
"""Tear down all AWS resources created by deploy.py."""
import boto3, time

REGION = "us-east-1"
CLUSTER = "crowd-map-cluster"
SERVICE = "crowd-map-svc"
REPO_NAME = "crowd-map"
SG_NAME = "crowd-map-sg"
LOG_GROUP = "/ecs/crowd-map-task"
TASK_FAM = "crowd-map-task"

ecs = boto3.client("ecs", region_name=REGION)
ecr = boto3.client("ecr", region_name=REGION)
ec2 = boto3.client("ec2", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)

print("=== Stopping service ===")
try:
    ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
    time.sleep(5)
    ecs.delete_service(cluster=CLUSTER, service=SERVICE, force=True)
    print("Deleted service")
except Exception as e:
    print(f"  {e}")

print("=== Deleting cluster ===")
try:
    ecs.delete_cluster(cluster=CLUSTER)
    print("Deleted cluster")
except Exception as e:
    print(f"  {e}")

print("=== Deregistering task definitions ===")
try:
    resp = ecs.list_task_definitions(familyPrefix=TASK_FAM)
    for arn in resp["taskDefinitionArns"]:
        ecs.deregister_task_definition(taskDefinition=arn)
        print(f"  Deregistered {arn}")
except Exception as e:
    print(f"  {e}")

print("=== Deleting ECR repo ===")
try:
    ecr.delete_repository(repositoryName=REPO_NAME, force=True)
    print("Deleted ECR repo")
except Exception as e:
    print(f"  {e}")

print("=== Deleting log group ===")
try:
    logs.delete_log_group(logGroupName=LOG_GROUP)
    print("Deleted log group")
except Exception as e:
    print(f"  {e}")

print("=== Deleting security group ===")
try:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = vpcs["Vpcs"][0]["VpcId"]
    sgs = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                 {"Name": "vpc-id", "Values": [vpc_id]}])
    for sg in sgs["SecurityGroups"]:
        ec2.delete_security_group(GroupId=sg["GroupId"])
        print(f"  Deleted {sg['GroupId']}")
except Exception as e:
    print(f"  {e}")

print("\nDone. All resources cleaned up.")
