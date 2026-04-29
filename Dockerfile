# AgentForge — production-targeted image for Railway (or any container host).
#
# Strategy:
#   - Base on openemr/openemr:flex-edge. The flex image ships the PHP +
#     Apache + extensions + entrypoint that handle DB-install, OAuth2 key
#     generation, composer install, and the npm build on first boot.
#   - COPY our forked source into /openemr. The flex entrypoint will
#     hoist this into /var/www/localhost/htdocs/openemr at startup.
#   - Configuration (DB host/credentials, admin user) is injected via
#     environment variables at runtime — Railway's MySQL plugin sets
#     most of these automatically.
#
# To build locally:
#   docker build -t agentforge-openemr .
#
# Required runtime env vars (set in Railway dashboard or `railway vars`):
#   MYSQL_HOST            (Railway: ${{ MySQL.MYSQL_PRIVATE_HOST }})
#   MYSQL_DATABASE        (Railway: ${{ MySQL.MYSQL_DATABASE }})
#   MYSQL_USER            (Railway: ${{ MySQL.MYSQL_USER }})
#   MYSQL_PASS            (Railway: ${{ MySQL.MYSQL_PASSWORD }})
#   MYSQL_ROOT_USER       root
#   MYSQL_ROOT_PASS       (Railway: ${{ MySQL.MYSQL_ROOT_PASSWORD }})
#   OE_USER               admin
#   OE_PASS               <generated, store in Railway secret>

FROM openemr/openemr:flex-edge

# Tell the flex entrypoint to use our COPY'd source and to run the
# composer/npm build on first boot.
ENV EASY_DEV_MODE=yes
ENV EASY_DEV_MODE_NEW=yes

# Production-mode flag — flips off cache-busting JS includes (see
# version.php:49) and any other dev-only behavior.
ENV OPENEMR__ENVIRONMENT=prod

# Bring our forked source in. This becomes the input to the flex
# entrypoint on container start.
COPY --chown=root:root . /openemr

# Apache listens here in the upstream image; Railway maps PORT → this.
EXPOSE 80
