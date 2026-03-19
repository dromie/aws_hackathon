#!/usr/bin/env python3
"""
Deploy or destroy crowd-map server on AWS ECS Fargate.

Usage:
    python deploy.py deploy   # create all resources and deploy
    python deploy.py destroy  # tear down everything
"""
import boto3, json, subprocess, sys, time

REGION    = "us-east-1"
REPO_NAME = "crowd-map"
CLUSTER   = "crowd-map-cluster"
SERVICE   = "crowd-map-svc"
TASK_FAM  = "crowd-map-task"
CONTAINER = "crowd-map"
PORT      = 8765
CPU       = "512"
MEMORY    = "1024"
ARCH      = "ARM64"
SG_NAME   = "crowd-map-sg"
ROLE_NAME = "ecsTaskExecutionRole"
LOG_GROUP = f"/ecs/{TASK_FAM}"
TAG_KEY   = "project"
TAG_VALUE = "crowd-map"

TAGS     = [{"Key": TAG_KEY, "Value": TAG_VALUE}]
ECS_TAGS = [{"key": TAG_KEY, "value": TAG_VALUE}]

sts     = boto3.client("sts", region_name=REGION)
ACCOUNT = sts.get_caller_identity()["Account"]
ECR_URI = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
IMAGE   = f"{ECR_URI}/{REPO_NAME}:latest"

ecr  = boto3.client("ecr", region_name=REGION)
ec2  = boto3.client("ec2", region_name=REGION)
ecs  = boto3.client("ecs", region_name=REGION)
iam  = boto3.client("iam", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)

POLICY_ARN = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
TRUST = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow",
                   "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                   "Action": "sts:AssumeRole"}]
})


def run(cmd):
    print(f"  $ {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr); sys.exit(1)
    return r.stdout.strip()


def get_vpc_id():
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    return vpcs["Vpcs"][0]["VpcId"]


# ──────────────────────────── DEPLOY ────────────────────────────

def deploy():
    # 1. ECR
    print("\n=== 1. ECR repository ===")
    try:
        ecr.create_repository(repositoryName=REPO_NAME, tags=TAGS)
        print(f"Created {REPO_NAME}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"Exists: {REPO_NAME}")

    # 2. Docker build & push
    print("\n=== 2. Build & push Docker image ===")
    pwd = run(f"aws ecr get-login-password --region {REGION}")
    run(f"echo '{pwd}' | docker login --username AWS --password-stdin {ECR_URI}")
    run(f"docker build -t {REPO_NAME} /workshop")
    run(f"docker tag {REPO_NAME}:latest {IMAGE}")
    run(f"docker push {IMAGE}")

    # 3. IAM execution role
    print("\n=== 3. ECS execution role ===")
    try:
        iam.create_role(RoleName=ROLE_NAME,
                        AssumeRolePolicyDocument=TRUST,
                        Description="ECS task execution role",
                        Tags=[{"Key": TAG_KEY, "Value": TAG_VALUE}])
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=POLICY_ARN)
        print(f"Created {ROLE_NAME}")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"Exists: {ROLE_NAME}")
    role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]

    # 4. CloudWatch log group
    print("\n=== 4. Log group ===")
    try:
        logs.create_log_group(logGroupName=LOG_GROUP, tags={TAG_KEY: TAG_VALUE})
        print(f"Created {LOG_GROUP}")
    except logs.exceptions.ResourceAlreadyExistsException:
        print(f"Exists: {LOG_GROUP}")

    # 5. Security group
    print("\n=== 5. Security group ===")
    vpc_id = get_vpc_id()
    sgs = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                 {"Name": "vpc-id", "Values": [vpc_id]}])
    if sgs["SecurityGroups"]:
        sg_id = sgs["SecurityGroups"][0]["GroupId"]
        print(f"Exists: {sg_id}")
    else:
        sg = ec2.create_security_group(
            GroupName=SG_NAME, Description="Crowd map server",
            VpcId=vpc_id,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": TAGS}])
        sg_id = sg["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{"IpProtocol": "tcp", "FromPort": PORT, "ToPort": PORT,
                            "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        print(f"Created: {sg_id}")

    # 6. Subnets
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                 {"Name": "default-for-az", "Values": ["true"]}])
    subnet_ids = [s["SubnetId"] for s in subnets["Subnets"][:2]]

    # 7. ECS cluster
    print("\n=== 6. ECS cluster ===")
    ecs.create_cluster(clusterName=CLUSTER, tags=ECS_TAGS)
    print(f"Cluster: {CLUSTER}")

    # 8. Task definition
    print("\n=== 7. Task definition ===")
    ecs.register_task_definition(
        family=TASK_FAM,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu=CPU, memory=MEMORY,
        executionRoleArn=role_arn,
        runtimePlatform={"cpuArchitecture": ARCH, "operatingSystemFamily": "LINUX"},
        containerDefinitions=[{
            "name": CONTAINER, "image": IMAGE,
            "portMappings": [{"containerPort": PORT, "protocol": "tcp"}],
            "essential": True,
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {"awslogs-group": LOG_GROUP,
                            "awslogs-region": REGION,
                            "awslogs-stream-prefix": "ecs"}
            }
        }],
        tags=ECS_TAGS)
    print(f"Registered: {TASK_FAM}")

    # 9. Service
    print("\n=== 8. Create/update service ===")
    try:
        existing = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])
        active = [s for s in existing["services"] if s["status"] == "ACTIVE"]
        if active:
            ecs.update_service(cluster=CLUSTER, service=SERVICE,
                               taskDefinition=TASK_FAM, forceNewDeployment=True)
            print("Updated existing service")
        else:
            raise Exception("create")
    except Exception:
        ecs.create_service(
            cluster=CLUSTER, serviceName=SERVICE,
            taskDefinition=TASK_FAM, desiredCount=1,
            launchType="FARGATE",
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED"}},
            tags=ECS_TAGS)
        print(f"Created service: {SERVICE}")

    # 10. Wait for public IP
    print("\n=== 9. Waiting for task to start ===")
    for attempt in range(60):
        tasks = ecs.list_tasks(cluster=CLUSTER, serviceName=SERVICE)
        if tasks["taskArns"]:
            desc = ecs.describe_tasks(cluster=CLUSTER, tasks=tasks["taskArns"])
            for t in desc["tasks"]:
                if t["lastStatus"] == "RUNNING":
                    for att in t.get("attachments", []):
                        for d in att.get("details", []):
                            if d["name"] == "networkInterfaceId":
                                eni = ec2.describe_network_interfaces(
                                    NetworkInterfaceIds=[d["value"]])
                                ip = eni["NetworkInterfaces"][0].get("Association", {}).get("PublicIp")
                                if ip:
                                    url = f"http://{ip}:{PORT}/crowd_map.html"
                                    print(f"\n{'='*50}")
                                    print(f"  LIVE: {url}")
                                    print(f"{'='*50}\n")
                                    return
        print(f"  Waiting... ({attempt+1}/60)")
        time.sleep(10)
    print("Timed out waiting for public IP. Check ECS console.")


# ──────────────────────────── DESTROY ───────────────────────────

def destroy():
    print("\n=== Stopping service ===")
    try:
        ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
        time.sleep(5)
        ecs.delete_service(cluster=CLUSTER, service=SERVICE, force=True)
        print("Deleted service")
    except Exception as e:
        print(f"  {e}")

    print("\n=== Deleting cluster ===")
    try:
        ecs.delete_cluster(cluster=CLUSTER)
        print("Deleted cluster")
    except Exception as e:
        print(f"  {e}")

    print("\n=== Deregistering task definitions ===")
    try:
        resp = ecs.list_task_definitions(familyPrefix=TASK_FAM)
        for arn in resp["taskDefinitionArns"]:
            ecs.deregister_task_definition(taskDefinition=arn)
            print(f"  Deregistered {arn}")
    except Exception as e:
        print(f"  {e}")

    print("\n=== Deleting ECR repo ===")
    try:
        ecr.delete_repository(repositoryName=REPO_NAME, force=True)
        print("Deleted ECR repo")
    except Exception as e:
        print(f"  {e}")

    print("\n=== Deleting log group ===")
    try:
        logs.delete_log_group(logGroupName=LOG_GROUP)
        print("Deleted log group")
    except Exception as e:
        print(f"  {e}")

    print("\n=== Deleting security group ===")
    try:
        vpc_id = get_vpc_id()
        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                     {"Name": "vpc-id", "Values": [vpc_id]}])
        for sg in sgs["SecurityGroups"]:
            ec2.delete_security_group(GroupId=sg["GroupId"])
            print(f"  Deleted {sg['GroupId']}")
    except Exception as e:
        print(f"  {e}")

    print("\nAll resources destroyed.")


# ──────────────────────────── MAIN ──────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("deploy", "destroy"):
        print("Usage: python deploy.py [deploy|destroy]")
        sys.exit(1)
    {"deploy": deploy, "destroy": destroy}[sys.argv[1]]()
