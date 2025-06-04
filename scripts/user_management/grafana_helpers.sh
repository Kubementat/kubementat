#!/usr/bin/env bash

function generate_password(){
  gpg --gen-random --armor 1 14
}

function construct_auth_header() {
    echo "Authorization: Bearer $GRAFANA_SERVICE_ACCOUNT_TOKEN"
}

function send_get_request() {
    path_with_params="$1"

    # TODO: refactor to use token instead of basic auth
    curl -X GET --header "Content-Type: application/json" \
        --header "Accept: application/json" \
        -u "$GRAFANA_ADMIN_USER:$GRAFANA_ADMIN_PASSWORD" \
        "$GRAFANA_BASE_URL$path_with_params"
}

function send_post_request() {
    path="$1"
    payload="$2"

    # TODO: refactor to use token instead of basic auth
    curl -X POST --header "Content-Type: application/json" \
        --header "Accept: application/json" \
        -u "$GRAFANA_ADMIN_USER:$GRAFANA_ADMIN_PASSWORD" \
        -d "$payload" \
        "$GRAFANA_BASE_URL$path"
}

function get_users_for_email() {
    email="$1"
    # GET /api/users/search?perpage=10&page=1&query=mygraf&sort=login-asc,email-asc  
    response="$(send_get_request "/api/users/search?perpage=10&page=1&query=$email&sort=email-asc")"
    echo "$response" | jq '.users[0]'
}

function create_user() {
    email="$1"
    name="$2"
    team_id="$3"
    password="$4"

    if [[ "$password" == "" ]]; then
        password="$(generate_password)"
    fi

    payload="{\"name\":\"$name\",\"email\":\"$email\",\"login\":\"$email\",\"password\":\"$password\"}"

    user_id="$(send_post_request "/api/admin/users" "$payload" | jq -r '.id')"

    echo "$(jq --null-input --arg user_id "$user_id" --arg password "$password" '{"user_id": $user_id, "password": $password}')"
}

function create_new_user_for_team() {
    email="$1"
    name="$2"
    team_id="$3"
    password="$4"

    existing_user=$(get_users_for_email "$email")
    if [[ "$existing_user" != "null" ]]; then
        echo "The user already exists, skipping user creation!"
        return 0
    fi

    create_user "$email" "$name" "$team_id" "$password"
}