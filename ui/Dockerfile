# The `ui` service: build the bundle, serve it as static files.
#
# The one thing that has to be right here is that VITE_* are BUILD args, not
# runtime variables. Vite inlines them into the bundle, so a value supplied at
# run time changes nothing and the UI ships pointing at localhost — which builds
# cleanly and fails only in a browser. That is the usual first-deploy failure.
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_API_URL
ARG VITE_SUPABASE_URL
# Supabase replaced the anon JWT with a publishable key. Both are accepted —
# `lib/supabase.ts` reads the new name and falls back — so a project created
# before the change still deploys.
ARG VITE_SUPABASE_PUBLISHABLE_KEY
ARG VITE_SUPABASE_ANON_KEY
ENV VITE_API_URL=$VITE_API_URL \
    VITE_SUPABASE_URL=$VITE_SUPABASE_URL \
    VITE_SUPABASE_PUBLISHABLE_KEY=$VITE_SUPABASE_PUBLISHABLE_KEY \
    VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY

RUN npm run build

# nginx:alpine substitutes ${PORT} into anything under /etc/nginx/templates at
# start-up, which is how a static server takes a port it is only told at run
# time. `$uri` survives because envsubst only replaces names that are actually
# in the environment.
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
