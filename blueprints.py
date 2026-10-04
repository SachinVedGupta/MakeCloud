"""Reviewed reference designs. Demo generates drafts only; no cloud operations."""
import json

CATALOG = [
    {'id': 'storage', 'name': 'Private object storage', 'clouds': ['aws', 'google', 'azurerm'], 'description': 'Encrypted private storage, optional versioning, and resource outputs.'},
    {'id': 'container', 'name': 'Container application', 'clouds': ['aws', 'google', 'azurerm'], 'description': 'Fargate, Cloud Run or Azure Container Apps, with explicit network exposure.'},
    {'id': 'static_site', 'name': 'Static site and CDN', 'clouds': ['aws'], 'description': 'Private S3 origin and HTTPS CloudFront distribution. Upload site assets after deployment.'},
    {'id': 'database', 'name': 'Private PostgreSQL', 'clouds': ['aws'], 'description': 'Private encrypted RDS, managed password, backups and production deletion protection.'},
]

def q(value):
    return json.dumps(value)

def normalize(code):
    """Expand compact native blocks; object/list commas retain expression meaning."""
    result, stack = [], []
    quoted, escaped, square, paren = False, False, 0, 0
    previous = ''
    for char in code:
        if quoted:
            result.append(char)
            if char == '"' and not escaped:
                quoted = False
            escaped = char == '\\' and not escaped
            continue
        if char == '"':
            quoted = True
        elif char == '[':
            square += 1
        elif char == ']':
            square -= 1
        elif char == '(':
            paren += 1
        elif char == ')':
            paren -= 1
        elif char == '{':
            kind = 'object' if previous in ('=', '(', '[', ',', ':') else 'block'
            stack.append(kind)
            if kind == 'block':
                result.append('{\n')
                previous = char
                continue
        elif char == '}':
            kind = stack.pop()
            if kind == 'block':
                result.append('\n}\n')
                previous = char
                continue
        elif char == ',' and stack and stack[-1] == 'block' and square == 0 and paren == 0:
            result.append('\n')
            previous = char
            continue
        result.append(char)
        if not char.isspace():
            previous = char
    return ''.join(result)

def header(s):
    provider = s['cloud']
    version = {'aws': '5.0', 'google': '6.0', 'azurerm': '4.0'}[provider]
    body = f'region = {q(s["region"])}'
    if provider == 'google':
        body += f'\n  project = {q(s["project_id"])}'
    elif provider == 'azurerm':
        body = f'features {{}}\n  subscription_id = {q(s["subscription_id"])}'
    return f'''terraform {{
  required_version = ">= 1.5.0"
  required_providers {{
    {provider} = {{ source = "hashicorp/{provider}", version = "~> {version}" }}
  }}
}}
provider "{provider}" {{
  {body}
}}
'''

def storage(s):
    name, cloud = s['name'], s['cloud']
    enabled = 'Enabled' if s['versioning'] else 'Suspended'
    if cloud == 'aws':
        return f'''resource "aws_s3_bucket" "storage" {{
  bucket_prefix = "makecloud-{name}-"
  force_destroy = false
  tags = {{ Project = "{name}", Environment = "{s['environment']}" }}
}}
resource "aws_s3_bucket_public_access_block" "private" {{
  bucket = aws_s3_bucket.storage.id
  block_public_acls = true
  block_public_policy = true
  ignore_public_acls = true
  restrict_public_buckets = true
}}
resource "aws_s3_bucket_versioning" "versioning" {{
  bucket = aws_s3_bucket.storage.id
  versioning_configuration {{ status = "{enabled}" }}
}}
resource "aws_s3_bucket_server_side_encryption_configuration" "encryption" {{
  bucket = aws_s3_bucket.storage.id
  rule {{ apply_server_side_encryption_by_default {{ sse_algorithm = "AES256" }} }}
}}
output "bucket_name" {{ value = aws_s3_bucket.storage.id }}
output "bucket_arn" {{ value = aws_s3_bucket.storage.arn }}
'''
    if cloud == 'google':
        return f'''resource "google_storage_bucket" "storage" {{
  name = "{s['project_id']}-{name}"
  location = "{s['region']}"
  uniform_bucket_level_access = true
  public_access_prevention = "enforced"
  force_destroy = false
  versioning {{ enabled = {str(s['versioning']).lower()} }}
  labels = {{ project = "{name}", environment = "{s['environment']}" }}
}}
output "bucket_name" {{ value = google_storage_bucket.storage.name }}
'''
    return f'''resource "azurerm_resource_group" "project" {{ name = "{name}-rg", location = "{s['region']}" }}
resource "azurerm_storage_account" "storage" {{
  name = "{name}"
  resource_group_name = azurerm_resource_group.project.name
  location = azurerm_resource_group.project.location
  account_tier = "Standard"
  account_replication_type = "{'GRS' if s['environment'] == 'production' else 'LRS'}"
  min_tls_version = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled = false
  blob_properties {{ versioning_enabled = {str(s['versioning']).lower()} }}
}}
resource "azurerm_storage_container" "private" {{
  name = "private"
  storage_account_id = azurerm_storage_account.storage.id
  container_access_type = "private"
}}
output "storage_account" {{ value = azurerm_storage_account.storage.name }}
'''

def website(s):
    # The origin stays private; CloudFront signs requests through OAC.
    return storage(s) + '''resource "aws_cloudfront_origin_access_control" "site" {
  name = "${aws_s3_bucket.storage.id}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior = "always"
  signing_protocol = "sigv4"
}
resource "aws_cloudfront_distribution" "site" {
  enabled = true
  default_root_object = "index.html"
  price_class = "PriceClass_100"
  origin {
    domain_name = aws_s3_bucket.storage.bucket_regional_domain_name
    origin_id = "private-site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }
  default_cache_behavior {
    target_origin_id = "private-site"
    allowed_methods = ["GET", "HEAD", "OPTIONS"]
    cached_methods = ["GET", "HEAD"]
    viewer_protocol_policy = "redirect-to-https"
    compress = true
    forwarded_values { query_string = false
      cookies { forward = "none" }
    }
  }
  restrictions { geo_restriction { restriction_type = "none" } }
  viewer_certificate { cloudfront_default_certificate = true }
}
resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.storage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "cloudfront.amazonaws.com" }, Action = "s3:GetObject", Resource = "${aws_s3_bucket.storage.arn}/*", Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn } } }]
  })
}
output "website_url" { value = "https://${aws_cloudfront_distribution.site.domain_name}" }
'''

def network(s):
    return f'''data "aws_availability_zones" "available" {{ state = "available" }}
resource "aws_vpc" "project" {{
  cidr_block = "10.42.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support = true
  tags = {{ Name = "{s['name']}" }}
}}
resource "aws_subnet" "private" {{
  count = 2
  vpc_id = aws_vpc.project.id
  cidr_block = cidrsubnet(aws_vpc.project.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
}}
'''

def database(s):
    prod = s['environment'] == 'production'
    return f'''resource "aws_security_group" "database" {{
  name_prefix = "{s['name']}-db-"
  vpc_id = aws_vpc.project.id
  description = "Private PostgreSQL; access must be explicitly granted"
}}
resource "aws_db_subnet_group" "database" {{
  name_prefix = "{s['name']}-"
  subnet_ids = aws_subnet.private[*].id
}}
resource "aws_db_instance" "database" {{
  identifier = "{s['name']}-postgres"
  engine = "postgres"
  instance_class = "db.t3.micro"
  allocated_storage = 20
  max_allocated_storage = 100
  storage_encrypted = true
  username = "appadmin"
  manage_master_user_password = true
  iam_database_authentication_enabled = true
  db_subnet_group_name = aws_db_subnet_group.database.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible = false
  multi_az = {str(prod).lower()}
  backup_retention_period = {14 if prod else 7}
  deletion_protection = {str(prod).lower()}
  skip_final_snapshot = false
  final_snapshot_identifier = "{s['name']}-final"
  copy_tags_to_snapshot = true
}}
output "database_endpoint" {{ value = aws_db_instance.database.endpoint }}
output "database_secret_arn" {{ value = aws_db_instance.database.master_user_secret[0].secret_arn }}
'''

def aws_container(s):
    ssl = bool(s.get('certificate_arn'))
    listener = 443 if ssl else 80
    endpoint = s.get("domain_name") if ssl else None
    code = network(s) + f'''resource "aws_internet_gateway" "project" {{ vpc_id = aws_vpc.project.id }}
resource "aws_subnet" "public" {{
  count = 2
  vpc_id = aws_vpc.project.id
  cidr_block = cidrsubnet(aws_vpc.project.cidr_block, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = false
}}
resource "aws_route_table" "public" {{
  vpc_id = aws_vpc.project.id
  route {{ cidr_block = "0.0.0.0/0", gateway_id = aws_internet_gateway.project.id }}
}}
resource "aws_route_table_association" "public" {{ count = 2, subnet_id = aws_subnet.public[count.index].id, route_table_id = aws_route_table.public.id }}
resource "aws_security_group" "load_balancer" {{
  name_prefix = "{s['name']}-lb-"
  vpc_id = aws_vpc.project.id
  ingress {{ from_port = {listener}, to_port = {listener}, protocol = "tcp", cidr_blocks = [{q('0.0.0.0/0' if s['public'] else '10.42.0.0/16')}] }}
  egress {{ from_port = 0, to_port = 0, protocol = "-1", cidr_blocks = ["0.0.0.0/0"] }}
}}
resource "aws_security_group" "app" {{
  name_prefix = "{s['name']}-app-"
  vpc_id = aws_vpc.project.id
  ingress {{ from_port = {s['port']}, to_port = {s['port']}, protocol = "tcp", security_groups = [aws_security_group.load_balancer.id] }}
  egress {{ from_port = 0, to_port = 0, protocol = "-1", cidr_blocks = ["0.0.0.0/0"] }}
}}
resource "aws_lb" "app" {{ name = "{s['name']}-lb", internal = {str(not s['public']).lower()}, load_balancer_type = "application", subnets = aws_subnet.public[*].id, security_groups = [aws_security_group.load_balancer.id] }}
resource "aws_lb_target_group" "app" {{ name = "{s['name']}-app", port = {s['port']}, protocol = "HTTP", target_type = "ip", vpc_id = aws_vpc.project.id
  health_check {{ path = "/", matcher = "200-399" }}
}}
resource "aws_lb_listener" "app" {{
  load_balancer_arn = aws_lb.app.arn
  port = {listener}
  protocol = "{'HTTPS' if ssl else 'HTTP'}"
  {('certificate_arn = '+q(s['certificate_arn'])) if ssl else ''}
  default_action {{ type = "forward", target_group_arn = aws_lb_target_group.app.arn }}
}}
resource "aws_ecs_cluster" "app" {{ name = "{s['name']}" }}
resource "aws_cloudwatch_log_group" "app" {{ name = "/makecloud/{s['name']}", retention_in_days = 30 }}
resource "aws_iam_role" "execution" {{
  name_prefix = "{s['name']}-exec-"
  assume_role_policy = jsonencode({{ Version = "2012-10-17", Statement = [{{ Effect = "Allow", Action = "sts:AssumeRole", Principal = {{ Service = "ecs-tasks.amazonaws.com" }} }}] }})
}}
resource "aws_iam_role_policy_attachment" "execution" {{ role = aws_iam_role.execution.name, policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" }}
resource "aws_ecs_task_definition" "app" {{
  family = "{s['name']}"
  network_mode = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu = "256"
  memory = "512"
  execution_role_arn = aws_iam_role.execution.arn
  container_definitions = jsonencode([{{ name = "app", image = {q(s['image'])}, essential = true, portMappings = [{{ containerPort = {s['port']}, protocol = "tcp" }}], logConfiguration = {{ logDriver = "awslogs", options = {{ "awslogs-group" = aws_cloudwatch_log_group.app.name, "awslogs-region" = {q(s['region'])}, "awslogs-stream-prefix" = "app" }} }} }}])
}}
resource "aws_ecs_service" "app" {{
  name = "{s['name']}"
  cluster = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count = {max(s['replicas'], 2) if s['environment'] == 'production' else s['replicas']}
  launch_type = "FARGATE"
  network_configuration {{ subnets = aws_subnet.public[*].id, security_groups = [aws_security_group.app.id], assign_public_ip = true }}
  load_balancer {{ target_group_arn = aws_lb_target_group.app.arn, container_name = "app", container_port = {s['port']} }}
  depends_on = [aws_lb_listener.app, aws_iam_role_policy_attachment.execution, aws_route_table_association.public]
}}
output "load_balancer_dns_name" {{ value = aws_lb.app.dns_name }}
output "app_url" {{ value = "{'https' if ssl else 'http'}://{endpoint if endpoint else '${aws_lb.app.dns_name}'}" }}
'''
    if s['database']:
        code += database(s) + f'''resource "aws_security_group_rule" "app_database" {{ type = "ingress", from_port = 5432, to_port = 5432, protocol = "tcp", security_group_id = aws_security_group.database.id, source_security_group_id = aws_security_group.app.id }}
'''
    return code

def container(s):
    if s['cloud'] == 'aws':
        return aws_container(s)
    if s['database']:
        raise ValueError('Built-in app plus PostgreSQL currently supports AWS. Choose AWS, omit the database, or use live AI for a custom design.')
    name = s['name']
    if s['cloud'] == 'google':
        code = f'''resource "google_project_service" "run" {{ project = "{s['project_id']}", service = "run.googleapis.com", disable_on_destroy = false }}
resource "google_cloud_run_v2_service" "app" {{
  name = "{name}"
  location = "{s['region']}"
  deletion_protection = {str(s['environment'] == 'production').lower()}
  ingress = "{'INGRESS_TRAFFIC_ALL' if s['public'] else 'INGRESS_TRAFFIC_INTERNAL_ONLY'}"
  template {{
    scaling {{ min_instance_count = {s['replicas'] if s['environment'] == 'production' else 0}, max_instance_count = 10 }}
    containers {{ image = {q(s['image'])}
      ports {{ container_port = {s['port']} }}
      resources {{ limits = {{ cpu = "1", memory = "512Mi" }} }}
    }}
  }}
  depends_on = [google_project_service.run]
}}
output "app_url" {{ value = google_cloud_run_v2_service.app.uri }}
'''
        if s['public']:
            code += '''resource "google_cloud_run_v2_service_iam_member" "public" { project = google_cloud_run_v2_service.app.project, location = google_cloud_run_v2_service.app.location, name = google_cloud_run_v2_service.app.name, role = "roles/run.invoker", member = "allUsers" }
'''
        return code
    return f'''resource "azurerm_resource_group" "project" {{ name = "{name}-rg", location = "{s['region']}" }}
resource "azurerm_container_app_environment" "project" {{ name = "{name}-env", location = azurerm_resource_group.project.location, resource_group_name = azurerm_resource_group.project.name }}
resource "azurerm_container_app" "app" {{
  name = "{name}"
  container_app_environment_id = azurerm_container_app_environment.project.id
  resource_group_name = azurerm_resource_group.project.name
  revision_mode = "Single"
  template {{ min_replicas = {s['replicas'] if s['environment'] == 'production' else 0}, max_replicas = 10
    container {{ name = "app", image = {q(s['image'])}, cpu = 0.25, memory = "0.5Gi" }}
  }}
  ingress {{ external_enabled = {str(s['public']).lower()}, target_port = {s['port']}, allow_insecure_connections = false
    traffic_weight {{ percentage = 100, latest_revision = true }}
  }}
}}
output "app_url" {{ value = "https://${{azurerm_container_app.app.ingress[0].fqdn}}" }}
'''

def build(spec):
    workload = spec['workload']
    if workload in ('static_site', 'database') and spec['cloud'] != 'aws':
        raise ValueError('This reference design currently supports AWS. Choose AWS or configure live AI for a custom cloud design.')
    if workload == 'storage' and spec.get('public'):
        raise ValueError('Object storage blueprints are private. Choose a static website for a public site, or use live AI with explicit access requirements.')
    if workload == 'static_site' and not spec['public']:
        raise ValueError('The CDN website blueprint is public over HTTPS with a private bucket origin. Choose private storage if no public site is intended.')
    content = {'storage': storage, 'static_site': website, 'container': container, 'database': lambda s: network(s) + database(s)}.get(workload)
    if content is None:
        raise ValueError('Custom infrastructure requires live AI mode.')
    summary = f"{workload.replace('_', ' ').title()} on {spec['cloud']} in {spec['region']}. Environment: {spec['environment']}. Costs have not been estimated."
    if workload == 'static_site':
        summary += ' Upload your index.html and assets to the bucket after deployment; infrastructure does not build your site.'
    if workload == 'container' and spec['database']:
        summary += ' PostgreSQL networking is configured, but your application must fetch its managed secret and use the database endpoint; app code and migrations are not deployed automatically.'
    if workload in ('database', 'container') and spec['environment'] == 'production':
        summary += ' Review deletion protection and final snapshots before any cleanup plan.'
    if workload == 'container' and spec['cloud'] == 'aws' and spec.get('certificate_arn'):
        if not spec.get('domain_name'):
            raise ValueError('Provide the hostname covered by your certificate and configure its DNS after deployment.')
        summary += f" Point {spec['domain_name']} at the load_balancer_dns_name output with your DNS provider. The certificate must cover that hostname; its coverage is not checked by MakeCloud."
    if workload == 'container' and spec['cloud'] == 'aws' and not spec.get('certificate_arn'):
        summary += ' Development load balancer uses HTTP; provide a regional ACM certificate for HTTPS.'
    return normalize(header(spec) + content(spec)), summary
