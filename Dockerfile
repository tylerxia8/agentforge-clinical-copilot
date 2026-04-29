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

# Preserve the base image's entrypoint script before overwriting /openemr
# with our forked source.
RUN cp /openemr/openemr.sh /tmp/openemr.sh

# Bring our forked source in. This becomes the input to the flex
# entrypoint on container start.
COPY --chown=root:root . /openemr

# Restore the entrypoint script that was overwritten by the COPY.
RUN cp /tmp/openemr.sh /openemr/openemr.sh && chmod +x /openemr/openemr.sh

# Install Node.js dependencies so that the gulp SCSS build (run by the
# flex-edge entrypoint at startup) has all required assets — including
# napa-downloaded packages like select2-bootstrap4-theme, bootstrap-rtl,
# jquery-ui, etc.
WORKDIR /openemr
RUN npm install

# Apache listens here in the upstream image; Railway maps PORT → this.
EXPOSE 80
