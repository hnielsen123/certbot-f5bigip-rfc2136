# F5 BIG-IP Device Certificate Automation via Certbot-DNS-RFC2136 

This Python script automates the process of generating SSL certificates using Certbot with the DNS-01 challenge (via the RFC2136 plugin for dynamic DNS updates) and deploying them to an F5 BIG-IP load balancer. The script handles both SSL certificates for services behind the load balancer (traffic certificates) and the device certificate for the F5 management interface itself.

## Installation

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/hnielsen123/certbot-f5bigip-rfc2136.git
   cd certbot-f5bigip-rfc2136
   ```

2. **Dependencies:**

    - This script assumes you are running from a Linux machine with Python 3.7 or higher installed

    - This script uses certbot and the certbot-dns-rfc2136 plugin for Let's Encrypt certificate generation and renewal via Dynamic DNS. It assumes: 
        - that you have this process setup and working already 
        - that the script has access to your `rfc2136.ini` file 
        - that the machine you'll be running the script from can create dynamic DNS entries on your nameserver 
        - *For details on getting this setup, see https://certbot.eff.org/ and https://certbot-dns-rfc2136.readthedocs.io/en/stable/*

    - This script utilizes the [BIGREST Python SDK](https://github.com/f5-rahm/BIGREST/tree/master) by [Leonardo Souza](https://github.com/leonardobdes) and [Jason Rahm](https://github.com/f5-rahm) to communicate with the F5 device using F5's iControl REST API

    Other Dependencies: 
    - paramiko for SSH and SCP operations
    - scp for secure file transfer

Install dependencies (excluding certbot and certbot-dns-rfc2136):

```bash
sudo pip install -r requirements.txt
```

## Configuration

The script uses a configuration file (`config.ini`) to manage input arguments. Here's an example:

```ini
[f5]
host = your_f5_host
user = your_f5_user
pass = your_f5_password
base_ssl_profile = "Common/clientssl-letsencrypt"

[certbot]
credentials = /path/to/rfc2136.ini
email = admin@example.com

[domains]
domains_list = app.domain.com, thing.domain.com, device-cert:domain.com
```

- F5 Section: Contains the credentials for the F5 BIG-IP device 
    - Must provide credentials that have administrator access to the device 
    - BaseSSLProfile defines the SSL profile that all new profiles created by this script will use as their parent profile. I recommend creating a `clientssl-letsencypt` profile (with `clientssl` as its parent), and using that as the parent. The new child profiles will be created with the naming scheme `certbot-{domain}`  
- Certbot Section: Configures Certbot, including the path to the `rfc2136.ini` credentials file and the email address for certificate notifications
- Domains Section: Lists the domains you want to generate certificates for, separated by commas
    - To generate and install the device certificate, add `device-cert:{yourdomain.com}` to the list of domains 

## Usage

Run the script with:

```bash
sudo python3 certbot-f5bigip-rfc2136.py /path/to/config.ini
```


### Workflow

- The script tests that certbot is installed and functional
- The script tests that it can successfully connect and authenticate to the F5 Rest API
- The script iterates through the list of domains provided in `config.ini`
- The script checks if a certificate for that domain already exists on the local machine
- If a certificate for that domain does not already exist, it will attempt to generate a new certificate for that domain using certbot and the certbot-dns-rfc2136 plugin
- If a certificate for that domain does already exist, it will attempt a certbot renewal. If certbot runs successfully, it will then check the last modification time for that certificate to determine whether the certificate was renewed, or if certbot just exited because the certificate did not need to be renewed yet
- The script then connects to the F5 REST API, and checks if an SSL profile for that domain exists already
- If so, it uploads the new certificate and replaces it as the certificate that profile uses
- If not, it creates a new SSL profile for that domain (with the naming scheme `certbot-{domain}`), uploads the new certificate (named `certbot-{domain}.crt` and `certbot-{domain}.key`), and then set the newly created SSL profile to use that certificate
- It then repeats this process with the next domain on the list
- If the domain `device-cert:{yourdomain.com}` is on the list of domains, the script checks to see if a wildcard cert for that domain exists on the local machine
- If the wildcard cert exists, it similarly attempts a certbot renewal. As before, if certbot runs successfully, it then checks the last modification time for that certificate to determine whether the certificate was renewed, or if certbot just exited because the certificate did not need to be renewed yet
- If the wildcard cert does not exist, the script requests a new wildcard certificate using certbot and certbot-dns-rfc2136
- The script then uses SCP to copy the wildcard cert and key to the F5 device (the F5 iControl REST API does not allow device certificates to be uploaded using the API; SCP is F5's recommended method for this use case) 
- The naming scheme for the device certificates is `domain-YYYY-MM-DD.crt` and `domain-YYYY-MM-DD.key`, e.g. for a wildcart cert for `*.example.com`, uploaded on 8/11/24, the certificate would be named `example-2024-08-11.crt`
- The script then connects to the API, and changes the configuration of httpd to use the newly uploaded certificate
- The script restarts the httpd service, so the new certificate will take effect
- The script continues with the next domain on the list (if any)

**Note:** At this time, for each new domain listed in `config.ini`, the script will create/upload a traffic certificate and create a new SSL profile on the F5 device, but it will not apply the SSL profile to a Virtual Server. After you've ran this script for a new domain, you must manually apply the new profile to the applicable Virtual Server. This should only have to be done the first time; on subsequent renewals the certificate will be swapped out in the same profile, so it should take effect without any manual interaction required.  

### Automation

TODO: detailed cronjob instructions, but should more or less be as simple as creating a cronjob that runs the script as root ~ once a day

## License
This project is licensed under the GPL-2.0 License. See the LICENSE file for details.


**Note:** *This script is provided as-is, without any warranty. Use it at your own risk, and make sure to test it thoroughly in your environment before deploying to production.*

