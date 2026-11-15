from clauz3.policy import domain_policy

POLICY = domain_policy(
    when_used="send_email",
    recommended=["only", "unique_recipients"],
    label="email",
)
