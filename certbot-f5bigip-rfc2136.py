#!/usr/bin/python3

import configparser
import logging
import os
import sys
import subprocess
from bigrest.bigip import BIGIP
import paramiko
from scp import SCPClient
from datetime import date
import argparse

def create_ssh_client(hostname, username, password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname, username=username, password=password)
    return ssh

def scp_transfer(ssh_client, local_path, remote_path, domain, file):
    with SCPClient(ssh_client.get_transport()) as scp:
        try:
            scp.put(local_path, remote_path)
            logger.info(f" + {domain}/{file} uploaded to F5 device")
        except SCPException as e:
            logger.error(f" + ERROR: SCP transfer failed for {domain}/{file}: {e}")
        except paramiko.SSHException as e:
            logger.error(f" + ERROR: SSH error during SCP transfer for {domain}/{file}: {e}")
        except Exception as e:
            logger.error(f" + ERROR: Unexpected error during SCP transfer for {domain}/{file}: {e}")

def load_config(config_file):
    config = configparser.ConfigParser()
    config.read(config_file)
    return config

def run_certbot(domain, certbot_config):
    # If the requested domain is *.example.com, filepath will be /etc/letsencrypt/live/example.com/
    if domain[0] == "*":
        cert_path = f'/etc/letsencrypt/live/{domain[2:]}/fullchain.pem'
        key_path = f'/etc/letsencrypt/live/{domain[2:]}/privkey.pem'
    # If requested domain is a subdomain, filepath will be /etc/letsencrypt/live/subdomain.example.com/
    else:
        cert_path = f'/etc/letsencrypt/live/{domain}/fullchain.pem'
        key_path = f'/etc/letsencrypt/live/{domain}/privkey.pem'

    # If force-upload mode, check if exists, if so skip cert generation/renewal and proceed to upload, if not exit on error
    if args.force_upload:
        if os.path.exists(cert_path):
            return cert_path, key_path
        else:
            logger.error(f'+ ERROR: --force-upload, certificate does not exist at {cert_path}')
            sys.exit(1)

    # Check if certificate already exists
    if os.path.exists(cert_path):
        logger.info(f' + Certificate for {domain} exists. Attempting renewal.')

        # Get modification time before renewal
        cert_mtime_before = os.path.getmtime(cert_path)

        # Attempt to renew certificate
        certbot_command = ['certbot', 'renew', '--cert-name', domain, '--dns-rfc2136']
        try:
            subprocess.run(certbot_command, check=True)

        except subprocess.CalledProcessError as e:
            logger.error(f" + ERROR: Certbot renewal failed for {domain} with error: {e}") 
            return None, None

        # Get modification time after attempted renewal
        cert_mtime_after = os.path.getmtime(cert_path)
        
        # Check to see if cert was modified, if it wasn't, but no certbot error was thrown, it means cert didn't need renewing
        if cert_mtime_before == cert_mtime_after:
            logger.info(f' + Certificate for {domain} did not need renewal.')
            return None, None

        logger.info(f" + New certificate created for {domain}. Certificate path: {cert_path} Key Path: {key_path}")

    else:
        logger.info(f" + No existing certificate for {domain}. Requesting new certificate.")

        # Request a new certificate using dns-rfc2136
        certbot_command = [
            'certbot', 'certonly', 
            '--key-type', 'rsa',
            '--dns-rfc2136', 
            '--dns-rfc2136-credentials', certbot_config['credentials'],
            '--domain', domain,
            '--non-interactive',
            '--agree-tos',
            '--email', certbot_config['email'] 
        ]

        try:
            subprocess.run(certbot_command, check=True)

        except subprocess.CalledProcessError as e:
            logger.error(f" + ERROR: Certbot failed create new cert for {domain} with error: {e}")
            return None, None

        logger.info(f" + Certificate renewed for {domain}")

    return cert_path, key_path


def instantiate_bigip(f5_config):
    return BIGIP(f5_config['host'], f5_config['user'], f5_config['pass'], session_verify=False)

def deploy_traffic_cert(domain, cert_path, key_path, f5_config):
    try:
        bigip = instantiate_bigip(f5_config)
        bigip.upload('/mgmt/shared/file-transfer/uploads', key_path)
        bigip.upload('/mgmt/shared/file-transfer/uploads', cert_path)
        key_status = bigip.exist(f'/mgmt/tm/sys/file/ssl-key/certbot-{domain}.key')
        cert_status = bigip.exist(f'/mgmt/tm/sys/file/ssl-cert/certbot-{domain}.crt')

        if key_status and cert_status:
            with bigip as transaction:
                modify_key = bigip.load(f'/mgmt/tm/sys/file/ssl-key/certbot-{domain}.key')
                modify_key.properties['sourcePath'] = f'file:/var/config/rest/downloads/{key_path.split("/")[-1]}'
                bigip.save(modify_key)
                modify_cert = bigip.load(f'/mgmt/tm/sys/file/ssl-cert/certbot-{domain}.crt')
                modify_cert.properties['sourcePath'] = f'file:/var/config/rest/downloads/{cert_path.split("/")[-1]}'
                bigip.save(modify_cert)
                logger.info(f' + Certificate and key for {domain} were successfully uploaded to F5.')

        else:
            keydata = {'name': f'certbot-{domain}.key', 'sourcePath': f'file:/var/config/rest/downloads/{key_path.split("/")[-1]}'}
            certdata = {'name': f'certbot-{domain}.crt', 'sourcePath': f'file:/var/config/rest/downloads/{cert_path.split("/")[-1]}'}
            bigip.create('/mgmt/tm/sys/file/ssl-key', keydata)
            bigip.create('/mgmt/tm/sys/file/ssl-cert', certdata)
            logger.info(f' + Certificate and key for {domain} were successfully uploaded to F5.')

        if not bigip.exist(f'/mgmt/tm/ltm/profile/client-ssl/certbot-{domain}'):
            sslprofile = {
                'name' : f'certbot-{domain}',
                'defaultsFrom': (BaseSSLProfile),
                'certKeyChain': [{
                    'name': f'{domain}_0',
                    'cert': f'/Common/certbot-{domain}.crt',
                    'key': f'/Common/certbot-{domain}.key'
                }]
            }

            logger.info(sslprofile)
            bigip.create('/mgmt/tm/ltm/profile/client-ssl', sslprofile)
            logger.info(f' + New client-ssl profile "certbot-{domain}" created using new certificate and key.')

        else:
            logger.info(f" + Existing profile 'certbot-{domain}' updated with new certificate and key")
    
    except Exception as e:
        logger.error(f" + ERROR: Failed to deploy certificate for {domain}: {e}")

def deploy_device_cert(domain, cert_path, key_path, f5_config):
    today = str(today.today())
    target_cert_path = f"/config/httpd/conf/ssl.crt/{domain.split('.')[-2]}-{today}.crt"
    target_key_path = f"/config/httpd/conf/ssl.key/{domain.split('.')[-2]}-{today}.key"

    # Transfer new device certificate and key to F5 device via SCP
    try:
        ssh_client = create_ssh_client(f5_config['host'], f5_config['user'], f5_config['pass'])
        scp_transfer(ssh_client, cert_path, target_cert_path)
        scp_transfer(ssh_client, key_path, target_key_path)
        ssh_client.close()
        logger.info(f" + Device certificates successfully uploaded to F5 device.")

    except Exception as e:
        logger.error(f" + ERROR: Failed to upload device certificates to F5 device: {e}")

    bigip = instantiate_bigip(f5_config)
    
    # Configure F5 device to use new device certificate
    try:
        with bigip as transaction:
            modify_cert = bigip.load("/mgmt/tm/sys/httpd")
            modify_cert.properties['sslCertfile'] = target_cert_path
            modify_cert.properties['sslCertkeyfile'] = target_key_path
            bigip.save(modify_cert)
            logger.info(f" + Configuration changed to use new device certificate")

    except Exception as e:
        logger.error(f" + ERROR: Failed to update configuration to use new device certificate: {e}")

    restart_command = {"name": "httpd", "command": "restart"}

    # Restart httpd service for new certificate to take effect
    try:
        bigip.command('/mgmt/tm/sys/service', restart_command)
        logger.info(" + Service httpd restart on F5 device.")
    except Exception as e:
        logger.error(f" + ERROR: Failed to restart httpd service. Restart manually for new certificate to take effect: {e}")


if __name__ == '__main__':

    # Parse args
    parser = argparse.ArgumentParser(description='Automates the process of generating SSL certificates using certbot-dns-rfc2136 and deploying them to an F5 BIG-IP load balancer. Handles both traffic and device certificates.')
    parser.add_argument('-c', '--config', type=str, required=True, help='Path to config.ini file')
    parser.add_argument('--force-upload', required=False, action='store_true', help='If this flag is provided, script will upload any existing letsencrypt certs for the provided domain(s) without checking renewal status. Used for transitioning services that already use certbot to the F5 device')
    args = parser.parse_args()

    # Logging
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

    # Load configuration
    config_file = args.config
    config = load_config(config_file)

    f5_config = config['f5']
    certbot_config = config['certbot']
    domains = config['domains']['domains_list'].split(',')
    BaseSSLProfile = config['f5']['base_ssl_profile']

    # Check if Certbot is available
    try:
        subprocess.run(['certbot', '--version'], check=True)
        logger.info(" + Certbot installed and functioning")

    except subprocess.CalledProcessError as e:
        logger.error(f" + ERROR: Certbot is not available or not working correctly: {e}")
        sys.exit(1)

    # Check if F5 is reachable
    try:
        bigip = instantiate_bigip(f5_config)
        bigip.exist('/mgmt/tm/ltm')  # Test an API call
        logger.info(f" + Big IP API Available")

    except Exception as e:
        logger.error(f" + ERROR: Failed to connect to F5 BIG-IP: {e}")
        sys.exit(1)

    for domain in domains:
        # If "device-cert:domain.com" is in domains_list, the script will attempt
        # to generate a wildcard cert and upload it as the device cert
        if domain.split(":")[0] == "device-cert":
            wildcard_domain = f'*.{domain.split(":")[1]}'
            cert_path, key_path = run_certbot(wildcard_domain.strip(), certbot_config) 

            if cert_path is None or key_path is None:
                logger.info(" + Continuing with next domain...")
                continue

            try:
                deploy_device_cert(domain.strip(), cert_path, key_path, f5_config)
                logger.info(f' + New wildcard certificate {domain} successfully created/renewed and installed as F5 device cert')

            except Exception as e:
                logger.error(f" + ERROR: Failed to deploy device certificate for *.{domain}: {e}")
                continue

        else:
            # Run certbot to either issue or renew cert
            cert_path, key_path = run_certbot(domain.strip(), certbot_config)

            if cert_path is None or key_path is None:
                logger.info(" + Continuing with next domain...")
                continue

            # Deploy cert to F5
            try:
                deploy_traffic_cert(domain.strip(), cert_path, key_path, f5_config)
                logger.info(f' + New certificate and key for {domain} successfully created/renewed and installed')

            except Exception as e:
                logger.error(f" + ERROR: Failed to deploy traffic certificate for {domain}: {e}")
                continue

