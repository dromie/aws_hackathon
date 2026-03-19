#!/usr/bin/env python3
"""Deploy crowd-map server to AWS ECS Fargate."""
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

sts = boto3.client("sts", region_name=REGION)
ACCOUNT = sts.get_caller_identity()["Account"]
ECR_URI = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
IMAGE   = f"{ECR_URI}/{REPO_NAME}:latest"

ecr = boto3.client("ecr", region_name=REGION)
ec2 = boto3.client("ec2", region_name=REGION)
ecs = boto3.client("ecs", region_name=REGION)
iam = boto3.client("iam", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)


def run(cmd):
    print(f"  $ {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        sys.exit(1)
    return r.stdout.strip()


# --- 1. ECR ---
print("\n=== 1. ECR repository ===")
try:
    ecr.create_repository(repositoryName=REPO_NAME)
    print(f"Created {REPO_NAME}")
except ecr.exceptions.RepositoryAlreadyExistsException:
    print(f"Exists: {REPO_NAME}")

# --- 2. Docker build & push ---
print("\n=== 2. Build & push Docker image ===")
pwd = run("echo $(aws ecr get-login-password --region " + REGION + ")")
run(f"echo '{pwd}' | docker login --username AWS --password-stdin {ECR_URI}")
run(f"docker build -t {REPO_NAME} /workshop")
run(f"docker tag {REPO_NAME}:latest {IMAGE}")
run(f"docker push {IMAGE}")

# --- 3. IAM execution role ---
print("\n=== 3. ECS execution role ===")
ROLE_NAME = "ecsTaskExecutionRole"
POLICY_ARN = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
TRUST = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow",
                   "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                   "Action": "sts:AssumeRole"}]
})
try:
    role = iam.create_role(RoleName=ROLE_NAME,
                           AssumeRolePolicyDocument=TRUST,
                           Description="ECS task execution role")
    iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=POLICY_ARN)
    print(f"Created {ROLE_NAME}")
    time.sleep(10)  # wait for propagation
except iam.exceptions.EntityAlreadyExistsException:
    print(f"Exists: {ROLE_NAME}")
ROLE_ARN = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]

# --- 4. CloudWatch log group ---
print("\n=== 4. Log group ===")
LOG_GROUP = f"/ecs/{TASK_FAM}"
try:
    logs.create_log_group(logGroupName=LOG_GROUP)
    print(f"Created {LOG_GROUP}")
except logs.exceptions.ResourceAlreadyExistsException:
    print(f"Exists: {LOG_GROUP}")

# --- 5. Security group ---
print("\n=== 5. Security group ===")
SG_NAME = "crowd-map-sg"
vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
VPC_ID = vpcs["Vpcs"][0]["VpcId"]

sgs = ec2.describe_security_groups(
    Filters=[{"Name": "group-name", "Values": [SG_NAME]},
             {"Name": "vpc-id", "Values": [VPC_ID]}])
if sgs["SecurityGroups"]:
    SG_ID = sgs["SecurityGroups"][0]["GroupId"]
    print(f"Exists: {SG_ID}")
else:
    sg = ec2.create_security_group(GroupName=SG_NAME,
                                   Description="Crowd map server",
                                   VpcId=VPC_ID)
    SG_ID = sg["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=SG_ID,
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": PORT, "ToPort": PORT,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    print(f"Created: {SG_ID}")

# --- 6. Get subnets ---
subnets = ec2.describe_subnets(
    Filters=[{"Name": "vpc-id", "Values": [VPC_ID]},
             {"Name": "default-for-az", "Values": ["true"]}])
SUBNET_IDS = [s["SubnetId"] for s in subnets["Subnets"][:2]]

# --- 7. ECS cluster ---
print("\n=== 6. ECS cluster ===")
ecs.create_cluster(clusterName=CLUSTER)
print(f"Cluster: {CLUSTER}")

# --- 8. Task definition ---
print("\n=== 7. Task definition ===")
ecs.register_task_definition(
    family=TASK_FAM,
    networkMode="awsvpc",
    requiresCompatibilities=["FARGATE"],
    cpu=CPU, memory=MEMORY,
    executionRoleArn=ROLE_ARN,
    containerDefinitions=[{
        "name": CONTAINER,
        "image": IMAGE,
        "portMappings": [{"containerPort": PORT, "protocol": "tcp"}],
        "essential": True,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": LOG_GROUP,
                "awslogs-region": REGION,
                "awslogs-stream-prefix": "ecs"
            }
        }
    }]
)
print(f"Registered: {TASK_FAM}")

# --- 9. Service ---
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
            "subnets": SUBNET_IDS,
            "securityGroups": [SG_ID],
            "assignPublicIp": "ENABLED"
        }})
    print(f"Created service: {SERVICE}")

# --- 10. Wait for public IP ---
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
                                sys.exit(0)
    print(f"  Waiting... ({attempt+1}/60)")
    time.sleep(10)

print("Timed out waiting for public IP. Check ECS console.")
