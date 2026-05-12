"""Demo request model — enterprise inquiries that bypass self-service signup.

The self-service signup flow (POST /api/v1/auth/signup) now creates accounts
directly with a free trial.  This model is for the enterprise / custom-plan
demo request form that collects institution, role, and message for follow-up.

Model defined in app/routers/public.py:DemoRequest — should be moved here
when the router is refactored.
"""