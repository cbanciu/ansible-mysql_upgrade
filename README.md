###<strong>Ansible playbook to upgrade MySQL on RHEL/CentOS servers.</strong>
***
<strong>NOTES:</strong>

It runs on RedHat 5 6 and 7.  
It can upgrade from MySQL 5.1 up to MySQL 5.7  
It can upgrade from Percona 5.1 to Percona 5.7  
It can upgrade from MariaDB 5.5 to MariaDB 10.1  
It DOES NOT upgrade from one vendor to another such as from MySQL to Percona.  

***
<strong>USAGE:</strong> <br />

**ansible-playbook -i hosts mysql_upgrade.yml**

***


<strong>VARIABLES</strong>


**backup_method: [rsync|holland]** => MySQL DATADIR backup method.  
**backup_time:** => Value in seconds for long running jobs such as backups or mysql_upgrade. Defaults already set to over 2 hours.  
**upgraded_version: ["5.5", "5.6", "5.7", "10.0", "10.1"]** => What MySQL version would you like upgrading to?  
**my_cnf: /etc/my.cnf** => Full path to MySQL configuration file.  
***