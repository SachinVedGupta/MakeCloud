"""Named infrastructure requirements and deterministic conversation helpers."""
import re
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Cloud = Literal['aws', 'google', 'azurerm']
Workload = Literal['storage', 'static_site', 'container', 'database', 'custom']
PROVIDER_NAMES = {'aws': 'AWS', 'google': 'Google Cloud', 'azurerm': 'Azure'}
AZURE_REGIONS = set('eastus eastus2 westus westus2 westus3 centralus northcentralus southcentralus westcentralus canadacentral canadaeast brazilsouth northeurope westeurope uksouth ukwest francecentral francesouth germanywestcentral switzerlandnorth norwayeast swedencentral polandcentral italynorth spaincentral uaenorth southafricanorth centralindia southindia westindia eastasia southeastasia japaneast japanwest koreacentral koreasouth australiaeast australiasoutheast australiacentral newzealandnorth israelcentral qatarcentral'.split())

class Spec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cloud: Cloud | None = None
    workload: Workload | None = None
    region: str | None = None
    name: str | None = None
    project_id: str | None = None
    subscription_id: str | None = None
    environment: Literal['development', 'production'] = 'development'
    budget: float | None = Field(default=None, ge=0, le=1000000)
    budget_unknown: bool = False
    public: bool | None = None
    database: bool | None = None
    image: str | None = None
    certificate_arn: str | None = Field(default=None, max_length=200)
    domain_name: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    versioning: bool = True
    replicas: int = Field(default=1, ge=1, le=10)
    notes: str = Field(default='', max_length=6000)

    @field_validator('name')
    @classmethod
    def name_valid(cls, value):
        if value is not None and not re.fullmatch(r'[a-z][a-z0-9]{2,19}', value):
            raise ValueError('Project name must be 3–20 lowercase letters and digits, starting with a letter.')
        return value

    @field_validator('project_id')
    @classmethod
    def project_valid(cls, value):
        if value is not None and not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', value):
            raise ValueError('Google project ID must be 6–30 lowercase letters, digits or hyphens.')
        return value

    @field_validator('subscription_id')
    @classmethod
    def subscription_valid(cls, value):
        if value is not None:
            if not re.fullmatch(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}', value):
                raise ValueError('Azure subscription ID must be a complete UUID.')
            value = str(uuid.UUID(value))
        return value

    @field_validator('image')
    @classmethod
    def image_valid(cls, value):
        if value is not None and (not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9./:_@-]{2,250}', value) or '://' in value or ('@' in value and not re.search(r'@sha256:[a-fA-F0-9]{64}$', value))):
            raise ValueError('Enter a container image reference, without a URL scheme or credentials.')
        return value

    @field_validator('domain_name')
    @classmethod
    def domain_valid(cls, value):
        if value:
            value = value.lower().rstrip('.')
            if '.' not in value or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in value.split('.')) or value.split('.')[-1].isdigit():
                raise ValueError('Enter a DNS hostname such as app.example.com, without a URL scheme or path.')
        return value

    @field_validator('certificate_arn')
    @classmethod
    def certificate_valid(cls, value):
        if value and not re.fullmatch(r'arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/[a-f0-9-]{36}', value):
            raise ValueError('Enter an existing AWS ACM certificate ARN for this region.')
        return value


def validate_spec(data):
    spec = Spec.model_validate(data)
    if spec.region:
        cloud = spec.cloud
        valid = (cloud == 'aws' and re.fullmatch(r'(?:us|eu|ap|ca|sa|af|me|il|mx)-(?:east|west|central|north|south|northeast|southeast|southwest)-[1-9]', spec.region)) or (cloud == 'google' and re.fullmatch(r'(?:us|europe|asia|australia|southamerica|northamerica|africa|me)-(?:east|west|central|north|south|northeast|southeast)[1-9]', spec.region)) or (cloud == 'azurerm' and spec.region in AZURE_REGIONS)
        if cloud and not valid:
            raise ValueError('Use a region identifier for the selected cloud. Region availability is checked again by Terraform.')
    if spec.cloud == 'aws' and spec.certificate_arn and spec.region and spec.certificate_arn.split(':')[3] != spec.region:
        raise ValueError('The ACM certificate must be in the selected AWS region.')
    return spec.model_dump()


def missing(spec, mode='demo'):
    fields = [field for field in ('cloud', 'workload', 'region', 'name') if not spec.get(field)]
    if spec.get('cloud') == 'google' and not spec.get('project_id'):
        fields.append('project_id')
    if spec.get('cloud') == 'azurerm' and not spec.get('subscription_id'):
        fields.append('subscription_id')
    if spec.get('budget') is None and not spec.get('budget_unknown'):
        fields.append('budget')
    if spec.get('workload') in ('container', 'static_site') and spec.get('public') is None:
        fields.append('public')
    if spec.get('workload') == 'container':
        if not spec.get('image'):
            fields.append('image')
        if spec.get('port') is None:
            fields.append('port')
        if spec.get('database') is None:
            fields.append('database')
        if spec.get('cloud') == 'aws' and spec.get('environment') == 'production' and spec.get('public') and not spec.get('certificate_arn'):
            fields.append('certificate_arn')
        if spec.get('cloud') == 'aws' and spec.get('certificate_arn') and not spec.get('domain_name'):
            fields.append('domain_name')
    if spec.get('workload') == 'custom' and mode == 'demo':
        fields.append('unsupported')
    return fields


def question(field, spec):
    cloud = spec.get('cloud')
    return {
        'cloud': 'Which cloud do you want to use: AWS, Azure, or Google Cloud? If you are unsure, ask me to compare them.',
        'workload': 'What are we building: private object storage, a static website, a container app, a PostgreSQL database, or something else?',
        'region': 'Which region should it run in? Examples: us-east-1 for AWS, eastus for Azure, us-central1 for Google Cloud.',
        'name': 'What project name should resources use? Choose 3–20 lowercase letters and digits, starting with a letter.',
        'project_id': 'What is the existing Google Cloud project ID? This is an identifier, not a credential.',
        'subscription_id': 'What is the Azure subscription UUID? Keep account credentials outside chat.',
        'budget': 'What is your monthly budget in USD? Say “unknown” if you want to decide later. I will not claim a cost estimate without pricing data.',
        'public': 'Should people on the internet be able to reach this app or website? Answer public or private.',
        'image': 'Which container image should run? Give a public image reference such as nginx:stable. For Google Cloud, use an image Cloud Run can pull.',
        'port': 'Which port does the application listen on inside the container? For example, nginx uses 80; many apps use 8080. This must match your image.',
        'database': 'Does this app need PostgreSQL? Say yes or no. The built-in app and database design is currently available on AWS.',
        'domain_name': 'Which hostname does that certificate cover, such as app.example.com? You must point its DNS record at the load balancer after deployment; its AWS hostname will not match your certificate.',
        'certificate_arn': 'For a public production AWS app, provide an existing ACM certificate ARN in this region so the load balancer can use HTTPS.',
        'unsupported': 'The local blueprints cover object storage, container apps, and AWS websites or PostgreSQL. Configure live AI for a custom design, or choose one of these workloads.',
    }.get(field, 'What other requirements should I consider?')


def extract(text, spec, awaiting=None):
    """Conservative extraction; questions are handled separately before this runs."""
    updates = {}
    lower = text.lower().strip()
    cloud = re.search(r'\b(aws|amazon web services|azure|gcp|google cloud)\b', lower)
    if cloud:
        updates['cloud'] = {'aws': 'aws', 'amazon web services': 'aws', 'azure': 'azurerm', 'gcp': 'google', 'google cloud': 'google'}[cloud.group()]
    region = re.search(r'\b(?:us|eu|ap|ca|sa|af|me|il|mx)-(?:east|west|central|north|south|northeast|southeast|southwest)-[1-9]\b|\b(?:us|europe|asia|australia|southamerica|northamerica|africa|me)-(?:east|west|central|north|south|northeast|southeast)[1-9]\b', lower)
    if region:
        updates['region'] = region.group()
    for region_name in AZURE_REGIONS:
        if re.search(r'\b' + region_name + r'\b', lower):
            updates['region'] = region_name
            break
    if re.search(r'\b(static (?:website|site)|cdn|cloudfront)\b', lower):
        updates['workload'] = 'static_site'
    elif re.search(r'\b(container|docker|web app|webapp|api|cloud run|fargate)\b', lower):
        updates['workload'] = 'container'
    elif re.search(r'\b(storage|bucket|blob)\b', lower):
        updates['workload'] = 'storage'
    elif re.search(r'\b(postgres(?:ql)?|database|rds)\b', lower):
        updates['workload'] = 'database'
    if re.search(r'\b(postgres(?:ql)?|with a database|with database)\b', lower):
        updates['database'] = True
    if re.search(r'\b(no database|without (?:a )?database)\b', lower):
        updates['database'] = False
    name = re.search(r'\b(?:named|called|name\s*[:=]|project name(?: is)?)\s*[\"\']?([a-z][a-z0-9]{2,19})\b', lower)
    if name:
        updates['name'] = name.group(1)
    project = re.search(r'\b(?:project[ _-]?id|gcp project|google project)(?:\s+is)?\s*[:=]?\s*[\"\']?([a-z][a-z0-9-]{4,28}[a-z0-9])\b', lower)
    if project:
        updates['project_id'] = project.group(1)
    subscription = re.search(r'\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b', text)
    if subscription:
        updates['subscription_id'] = subscription.group()
    budget = re.search(r'\$\s*(\d+(?:\.\d+)?)|(?:budget(?: of| is)?\s*[:=]?\s*)(\d+(?:\.\d+)?)', lower)
    if budget:
        updates.update(budget=float(budget.group(1) or budget.group(2)), budget_unknown=False)
    if re.search(r'\b(prod|production)\b', lower):
        updates['environment'] = 'production'
    if re.search(r'\b(dev|development|prototype)\b', lower):
        updates['environment'] = 'development'
    if re.search(r'\b(public|internet|publicly)\b', lower):
        updates['public'] = True
    if re.search(r'\b(private|internal|no public access)\b', lower):
        updates['public'] = False
    if re.search(r'\b(disable|without|no) versioning\b', lower):
        updates['versioning'] = False
    elif 'versioning' in lower or 'versioned' in lower:
        updates['versioning'] = True
    image = re.search(r'\bimage\s*[:=]?\s+([a-zA-Z0-9][a-zA-Z0-9./:_@-]{2,250})', text)
    if image:
        updates['image'] = image.group(1).rstrip('.,')
    certificate = re.search(r'arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/[a-f0-9-]{36}', text)
    if certificate:
        updates['certificate_arn'] = certificate.group()
    domain = re.search(r'\b(?:domain|hostname)(?: is)?\s*[:=]?\s+([a-zA-Z0-9.-]+)', text)
    if domain:
        updates['domain_name'] = domain.group(1).rstrip('.')
    port = re.search(r'\bport\s*[:=]?\s*(\d{1,5})\b', lower)
    if port:
        updates['port'] = int(port.group(1))
    replicas = re.search(r'\b(\d+)\s+(?:replicas|instances|tasks)\b|\breplicas\s*[:=]?\s*(\d+)', lower)
    if replicas:
        updates['replicas'] = int(replicas.group(1) or replicas.group(2))
    if awaiting and awaiting not in updates:
        value = text.strip()
        if awaiting in ('region', 'name', 'project_id', 'subscription_id', 'image', 'certificate_arn', 'domain_name'):
            if not updates and not re.search(r'\s', value):
                updates[awaiting] = value
        elif awaiting == 'budget' and lower in ('unknown', 'not sure', 'skip', 'none'):
            updates.update(budget=None, budget_unknown=True)
        elif awaiting == 'budget' and re.fullmatch(r'\d+(?:\.\d+)?', lower):
            updates['budget'] = float(lower)
        elif awaiting == 'port' and re.fullmatch(r'\d{1,5}', lower):
            updates['port'] = int(lower)
        elif awaiting == 'database' and lower in ('yes', 'no', 'none'):
            updates['database'] = lower == 'yes'
        elif awaiting == 'workload' and lower in ('something else', 'custom'):
            updates['workload'] = 'custom'
    if updates.get('cloud') and spec.get('cloud') and updates['cloud'] != spec['cloud']:
        updates.setdefault('region', None)
        updates.setdefault('project_id', None)
        updates.setdefault('subscription_id', None)
    return updates


def is_question(text):
    if re.match(r'^(?:can|could|would) you (?:please )?(?:create|build|make|add|update|change|generate|provision|deploy|remove|delete|destroy)\b', text.lower().strip()):
        return False
    if re.match(r'^(?:can|could|would) you (?:please )?(?:explain|compare|describe|summarize|tell me|show)\b', text.lower().strip()):
        return True
    return bool(re.match(r'^(why|what|how|can you explain|explain|compare|tell me|is it|does it|do i|which (?:cloud|region|option|one) should|what about)\b', text.lower().strip()) or text.rstrip().endswith('?'))


def explain(text, item):
    spec = item['spec']
    lower = text.lower()
    cloud = PROVIDER_NAMES.get(spec.get('cloud'), 'your selected cloud')
    if 'cost' in lower or 'cheap' in lower or 'budget' in lower:
        cost = (item.get('plan') or {}).get('cost') or {}
        if cost.get('status') == 'estimated' and cost.get('monthly') is not None:
            return f"The latest plan estimates {cost.get('currency', 'USD')} {cost['monthly']:.2f} per month. Usage, egress, taxes and unsupported resources can add charges. This is not a spending cap. Ask for a specific design change to review another proposal."
        return 'Pricing has not been measured yet. Storage costs depend on bytes stored, requests and egress. Container apps add compute; databases and load balancers add ongoing charges. Start with development capacity, avoid a database if it is unnecessary, and inspect a pricing estimate before applying. Tell me a budget or a concrete change and I can propose a revision without overwriting your current code.'
    if 'aws' in lower and ('gcp' in lower or 'azure' in lower or 'cloud' in lower):
        return 'Choose the cloud where you already have an account, credits and operational experience. AWS has broad infrastructure coverage; Google Cloud offers a direct Cloud Run path for containers; Azure fits existing Microsoft environments. MakeCloud can use any of these three. Tell me your choice and I will keep the design consistent with it.'
    if 'deploy' in lower or 'created' in lower or 'status' in lower:
        return f"This project is {item['status'].replace('_', ' ')}. Its latest configuration revision is {item['revision']}. A Terraform draft or plan does not mean resources exist. Only a completed apply establishes deployment, and outputs and checks are shown in the deployment panel."
    if 'terraform' in lower:
        return 'Terraform describes desired cloud resources. MakeCloud formats and validates the configuration, creates a saved plan, and asks you to review it. Apply executes that exact plan. State records the resources so later plans can update or deliberately delete the same infrastructure.'
    return f"For this project we are designing {spec.get('workload') or 'infrastructure'} on {cloud} in {spec.get('region') or 'a region still to be chosen'}. Object storage defaults to private access, encryption and versioning. Container designs need an image, network exposure and database decision. Production settings add availability and retention costs. I keep your accepted configuration unchanged when answering questions; a change is proposed separately for review."
