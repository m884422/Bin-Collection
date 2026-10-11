# Manchester bin-collection iCal feed

This project builds an iCalendar (`.ics`) subscription from Manchester City Council's live bin checker and republishes it with GitHub Actions + GitHub Pages.

The included `site/bin-collections.ics` is the live snapshot for the supplied property, refreshed from the council on 11 October 2026. It contains collection names and dates only; it does **not** include the home's address or UPRN.

## What the feed does

- Includes the **whole current month and the whole following month**, including earlier dates in the current month so the month view is complete.
- Combines same-date collections into one entry, titled **`Bin Collection - Grey, brown, green`** or **`Bin Collection - Blue`** as applicable. Event titles do not use the word “black”.
- Refreshes automatically once a day, targeted for **00:10 Europe/London**. The scheduled GitHub run is best-effort and may start a little late.
- Adds two iCalendar alerts to upcoming collections: **9:00pm the evening before** and **7:00am on collection day**. Past alert times are omitted so an update will not publish stale notifications.
- Retrieves dates directly from Manchester City Council's checker each time. If the council endpoint fails or returns no schedule, the job fails rather than publishing an empty calendar.

Calendar apps vary in how they handle alerts from subscribed calendars. This feed encodes both alerts, but confirm that your app allows notifications for an external subscription; some services ignore feed-provided alarms.

## Set up automatic publishing

1. Create a GitHub repository. A **public** GitHub Pages site is needed for a normal Calendar-by-URL subscription. Anyone who has the feed URL can see the bin dates, although the feed does not reveal the home's address or UPRN.
2. Before uploading the project files, open **Settings → Secrets and variables → Actions → New repository secret**. Add `MCC_UPRN` and set it to the UPRN provided separately. Do not put the UPRN in a committed source file.
3. Open **Settings → Pages** and set **Build and deployment → Source** to **GitHub Actions**.
4. Upload/commit the project files to the repository's `main` or `master` branch. That push automatically builds and publishes the first feed—there is **no manual “Run workflow” step**. After that, the scheduled workflow refreshes it daily at 00:10 Europe/London. If the files are already uploaded before the secret is added, the next scheduled run will publish it automatically.
5. In **Settings → Pages**, copy the site URL and append `/bin-collections.ics`. Subscribe to that full URL:
   - **Apple Calendar:** File → New Calendar Subscription…; ensure alerts are enabled for the subscription.
   - **Google Calendar:** Other calendars → From URL. Google may not use alarms embedded in external feed subscriptions; check an upcoming event's notification behavior.

The published URL will look like `https://YOUR-USER.github.io/YOUR-REPO/bin-collections.ics` for a normal project repository. Use the actual URL shown in GitHub Pages.

The workflow commits the refreshed `.ics` file to the repository and deploys it to Pages. Bot commits do not start a loop of extra builds.

## Local test

```bash
python -m pip install -r requirements.txt
export MCC_UPRN='YOUR_UPRN'
python generate_calendar.py --output site/bin-collections.ics
```

To reproduce the snapshot date used here:

```bash
MCC_UPRN='YOUR_UPRN' python generate_calendar.py --as-of 2026-10-11
```

## Source and set-out note

- Official Manchester bin-date checker: <https://manchester.portal.uk.empro.verintcloudservices.com/site/myaccount/request/sr_bin_coll_day_checker>
- Manchester City Council says bins should be outside and visible from the road by **7am** on collection day: <https://www.manchester.gov.uk/bins-rubbish-and-recycling/where-and-when-to-leave-your-bins>

Calendar apps choose their own subscription refresh interval, so an updated Pages file may not appear immediately. GitHub Pages makes the feed publicly reachable; do not publish it if you need the schedule to remain private.
