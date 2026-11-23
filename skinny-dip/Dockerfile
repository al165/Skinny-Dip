FROM node:26-alpine AS build

WORKDIR /site

COPY package*.json ./

RUN npm ci

COPY src/ ./src
COPY public/ ./public
COPY *.json ./
COPY index.html ./
COPY vite.config.ts ./

ARG VITE_DIP_API=/api

RUN npm run build


FROM nginx:alpine 

WORKDIR /site

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /site/dist /usr/share/nginx/html/

EXPOSE 8080

