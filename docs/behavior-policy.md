# Behavior Policy

## Retrieve first

Prefer local context. Retrieve when the user explicitly asks, the question is time-sensitive, or the available context is insufficient. Remote retrieval requires configuration permission.

## Ask first

Ask when the goal is materially ambiguous, required input is missing, or a high-impact choice cannot safely use a default. Do not ask users to choose minor reversible formatting or naming details when a project convention is available.

## Pause for approval

Pause before external messages, publishing, deletion, payments, production changes, or other configured side effects. Approval is a state transition, not a prompt suggestion.

## Continue to completion

A failed tool call becomes a retry, blocked, or failed state. It never silently becomes completed. A task checkpoint should contain enough structured state to resume without rereading an unbounded transcript.
