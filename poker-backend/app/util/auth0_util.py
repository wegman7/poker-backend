import http.client, json, os
from dotenv import load_dotenv

load_dotenv()

auth0_domain = os.getenv('AUTH0_DOMAIN')
audience = os.getenv('AUTH0_API_IDENTIFIER')
client_id = 'XFQQrnqBnCC6ceYh8NlIvOPSVr422j13'
# what is client secret for?
client_secret = 'XybVxyjuQipKBOzPXV6tqX2IEDdeyMSnugO7xS7dDyTY-rAhGVKJQ7bMkBbAkx8M'

def get_mang_token():
    conn = http.client.HTTPSConnection(auth0_domain)

    # these are management client_id and client_secret
    payload = "{\"client_id\":\"wJKkZJURtJchqA7OBDKJYw6SYZ7uziJt\",\"client_secret\":\"lr-AUBKEq4qv6mZ1HQj7WnkghoMd-RbcuLg6F_Mg8rL_7XAYR8JQVQNBaRVfrZ-X\",\"audience\":\"https://dev--9h7x0q1.us.auth0.com/api/v2/\",\"grant_type\":\"client_credentials\"}"

    headers = { 'content-type': "application/json" }

    conn.request("POST", "/oauth/token", payload, headers)

    res = conn.getresponse()
    data = res.read()

    decoded_data = data.decode("utf-8")
    json_data = json.loads(decoded_data)

    token = json_data.get('access_token')

    conn.close()

    return token

def get_user_token(username, password):
    # don't need client secret for user login
    url = f'{auth0_domain}/oauth/token'
    headers = {'Content-Type': 'application/json'}
    body = json.dumps({
        'grant_type': 'password',
        'client_id': client_id,
        'audience': audience,
        # you can change the audience to get different auth/id tokens
        # 'audience': 'https://dev--9h7x0q1.us.auth0.com/userinfo',
        'username': username,
        'password': password
    })

    # Create a connection and make the request
    conn = http.client.HTTPSConnection(auth0_domain)
    conn.request("POST", "/oauth/token", body, headers)

    # Get the response
    response = conn.getresponse()
    data = response.read()

    # Decode the response data
    decoded_data = data.decode("utf-8")
    json_data = json.loads(decoded_data)

    # Extract the token
    token = json_data.get('access_token')
    id_token = json_data.get('id_token')

    # Close the connection
    conn.close()

    return token

def get_user_details():
    conn = http.client.HTTPSConnection(auth0_domain)
    token = get_mang_token()

    # find user info
    id = 'auth0|620f0a8ce734fe006e76c97b'
    headers = {
        'content-type': "application/json",
        'authorization': 'Bearer ' + token
    }

    conn.request("GET", f"/api/v2/users/{id}", headers=headers)

    response = conn.getresponse()
    data = response.read()

    decoded_data = data.decode("utf-8")
    data = dict(json.loads(decoded_data))
    conn.close()

    return data