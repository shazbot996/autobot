# Google Cloud screen fixes from the first real run

Written outside cr2, in Claude Code, by the model claude-fable-5-2, on 2026-11-27.

Three requests from the owner's first run of the Google Cloud screen against the real project, quoted without the terminal output he pasted with them.

> when I access APIs, I get an error - even after I enabled them - the scripts worked well, but what is this error:

> closer - I'm not sure what to do to fix the weather api key, however... this is what the screen looks like now:

> ok everything is working but rate limits - not sure we can set that here anyway - now I get this: schiele@branch:~/projects/autobot$ bash run/gcp/quotas.sh
> ERROR: (gcloud.beta.quotas.preferences.create) NOT_FOUND: com.google.apps.framework.request.NotFoundException: Limit 'RequestsPerDayPerProject' does not exist or is not visible in the service 'weather.googleapis.com'. This command is authenticated as schiele@cambiumplatform.com which is the active account specified by the [core/account] property.

> the second command doesn't seem to finish - it just returns nothing and hangs here

With the last one he pasted the Weather API's quota ids as `gcloud beta quotas info list` printed them: for each of CurrentConditions, ForecastDays, ForecastHours, ForecastMinutes, HistoryHours, MapTile and PublicAlerts, a `QueriesPerDay`, a `QueriesPerMinute` and a `QueriesPerUser` quota.
