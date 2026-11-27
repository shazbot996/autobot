# Key step creates the service account first

Written outside cr2, in Claude Code, by the model claude-fable-5-2, on 2026-11-27.

> I ran the steps the missing key interface showed me, and this is what happens if you start there - my bash history:
>
> ```
> schiele@branch:~/projects/autobot$ # Nothing made here is readable by anyone else
> umask 077
>
> # The git-ignored directory the key is kept in
> mkdir -p secrets
>
> # Only you may enter it
> chmod 700 secrets
>
> # Create the account's key, straight into its file
> gcloud iam service-accounts keys create secrets/service-account-autobot-agent.json --iam-account=autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com \
> --project=schiele-autobot-dev
>
> # Only you may read the key
> chmod 600 secrets/service-account-autobot-agent.json
>
>   To look for yourself: these change nothing
> # List the account's keys
> gcloud iam service-accounts keys list --iam-account=autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com --project=schiele-autobot-dev
> ERROR: (gcloud.iam.service-accounts.keys.create) NOT_FOUND: Unknown service account. This command is authenticated as schiele@cambiumplatform.com which is the active account specified by the [core/account] property
> To: command not found
> ERROR: (gcloud.iam.service-accounts.keys.list) NOT_FOUND: Unknown service account. This command is authenticated as schiele@cambiumplatform.com which is the active account specified by the [core/account] property
> ```
>
> - this is because I haven't created the service account yet. we should add that to the guide and script.
