# import http.client

# conn = http.client.HTTPSConnection("dev--9h7x0q1.us.auth0.com")

# payload = "{\"client_id\":\"2LOMpNLdSgmFLE3lvsjyMDB5LNRtZDnr\",\"client_secret\":\"s9Wsd6gsHKWBv_7QA69Ft7iG2kDebIoLcLrE1mDU8mx-wdhslf2J7-SGWxHu7mzj\",\"audience\":\"http://localhost:8000\",\"grant_type\":\"client_credentials\"}"

# headers = { 'content-type': "application/json" }

# conn.request("POST", "/oauth/token", payload, headers)

# res = conn.getresponse()
# data = res.read()

# print(data.decode("utf-8"))

import http.client
import json

# Replace these variables with your actual details
auth0_domain = 'dev--9h7x0q1.us.auth0.com'
client_id = 'XFQQrnqBnCC6ceYh8NlIvOPSVr422j13'
client_secret = 'XybVxyjuQipKBOzPXV6tqX2IEDdeyMSnugO7xS7dDyTY-rAhGVKJQ7bMkBbAkx8M'
audience = 'http://localhost:8000'
username = 'wegman7@gmail.com'
password = 'Jfg717jw7!@#'

# Prepare the request details
url = f'{auth0_domain}/oauth/token'
headers = {'Content-Type': 'application/json'}
body = json.dumps({
    'grant_type': 'password',
    'client_id': client_id,
    'client_secret': client_secret,
    'audience': audience,
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

# Print the token
print("Token:", token)

# Close the connection
conn.close()
