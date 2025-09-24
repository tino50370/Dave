import boto3
from strands import Agent
from strands.models import BedrockModel
from botocore.config import Config

session = boto3.Session(profile_name="default", region_name="us-east-1")

bedrock = BedrockModel(
    model_id="openai.gpt-oss-120b-1:0",  # or another Bedrock model you enabled
    boto_session=session,                    # use your custom boto session
    streaming=True,                          # True (default) or False if your model/tool use requires it
    temperature=0.3,
    top_p=0.8,
    boto_client_config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    # Guardrails (optional)
    # guardrail_id="gr-xxxxxxxx", guardrail_version="1",
    # guardrail_trace="enabled_full",
)

agent = Agent(model=bedrock)
print(agent("Give me 2 product ideas for a DevOps copilot."))

# (Optional) pin a specific profile/region or client config