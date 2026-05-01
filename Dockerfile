# AgentForge — production-targeted image for Railway.
#
# Strategy:
#   - Base on the stable production image openemr/openemr:7.0.3. Source
#     is baked in; composer/npm have already run during image build, so
#     first-container-boot is just the DB schema install and key
#     generation (~1-2 min on Railway, vs ~10 min for flex variants).
#   - Configuration (DB host/credentials, admin user) is injected via
#     environment variables at runtime — Railway's MySQL plugin sets
#     most of these automatically.
#
# Note: this image runs the *upstream* OpenEMR source, NOT our fork's
# modifications (the AgentForge module + audit docs live in the GitHub
# repo, not in the deployed app for Tuesday's MVP). When the agent
# integration goes live for Thursday's deliverable, we'll switch to a
# custom build that COPYs our source on top.
#
# We tried openemr/openemr:flex-edge first; its ONBUILD instructions
# require the openemr.sh bootstrap script (lives in openemr-devops
# repo, not the application repo) and so failed with
# `cp: can't stat '/openemr/openemr.sh'`.
#
# To build locally:
#   docker build -t agentforge-openemr .
#
# Required runtime env vars (set in Railway dashboard):
#   MYSQL_HOST            ${{ MySQL.MYSQLHOST }}
#   MYSQL_PORT            ${{ MySQL.MYSQLPORT }}
#   MYSQL_DATABASE        ${{ MySQL.MYSQL_DATABASE }}
#   MYSQL_USER            ${{ MySQL.MYSQLUSER }}
#   MYSQL_PASS            ${{ MySQL.MYSQLPASSWORD }}
#   MYSQL_ROOT_USER       root
#   MYSQL_ROOT_PASS       ${{ MySQL.MYSQL_ROOT_PASSWORD }}
#   OE_USER               admin
#   OE_PASS               <generated, set in Railway>

FROM openemr/openemr:7.0.3

# Production-mode flag.
ENV OPENEMR__ENVIRONMENT=prod

# Work around non-idempotent auto_configure.php: if a previous deploy
# partially created the schema, drop and recreate the DB before setup.
COPY docker-entrypoint-wrapper.sh /docker-entrypoint-wrapper.sh
RUN chmod +x /docker-entrypoint-wrapper.sh
ENTRYPOINT ["/docker-entrypoint-wrapper.sh"]

# Bake the AgentForge Clinical Co-Pilot module into the image. After
# install via the OpenEMR Module Manager UI, this renders the chat
# panel into the patient chart. The module talks to the agent service
# at the URL set in `Globals → copilot_agent_url`.
#
# Permissions: OpenEMR runs as the `apache` user; we chown so the
# Module Manager can write to its own directory (it touches some
# generated files during install).
COPY --chown=apache:apache interface/modules/custom_modules/oe-module-clinical-copilot \
     /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-clinical-copilot

# Apache listens here in the upstream image; Railway maps PORT → this.
EXPOSE 80

# History — earlier attempts that did not work, kept here so we don't
# repeat them on Thursday:
#
#   1. `FROM openemr/openemr:flex-edge` — its ONBUILD instructions run
#      `cp /openemr/openemr.sh /tmp/openemr.sh` BEFORE our COPY, so they
#      fail because openemr.sh lives in the openemr-devops repo, not
#      the application repo we forked.
#
#   2. `FROM openemr/openemr:7.0.3` + manual save/COPY/restore of
#      openemr.sh — production image stores source at
#      /var/www/localhost/htdocs/openemr/, not /openemr/, so
#      `cp /openemr/openemr.sh /tmp/...` fails for the same reason.
#
# For Thursday's agent integration (which needs our fork's source on
# the deployed app), the right path is a multi-stage build: stage 1
# composer install + npm build against our source, stage 2 copy the
# built artifact into a fresh php:8.2-apache with all the OpenEMR
# extensions installed.
