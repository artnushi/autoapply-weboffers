# AutoApply WebOffers

Daily research workflow for potential website clients. It is discovery-only and does not send email.

## Required GitHub Actions secrets
- TINYFISH_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

Add these under repository Settings, Secrets and variables, Actions. Never commit secret values.

## Running
Open the Actions tab and run "Daily website prospect discovery" after adding all three secrets. The workflow is also scheduled for weekdays at 09:17 Europe/Belgrade time.

## Safety
- Maximum five newly saved prospects per run.
- Candidate records include source URLs and basic evidence.
- The website quality check is heuristic and must be manually verified.
- A publicly listed email is not proof of consent. The workflow does not send email or contact prospects.
- TinyFish Search is currently advertised as free without a credit card; see https://www.tinyfish.ai/free-search-fetch. Verify current limits and terms before production use.

## Offer
Website build: €800, including the first year of maintenance. Renewal: €200/year.
Portfolio: https://hirschen-buenzen.ch/
Main site: https://artnushi.com/
