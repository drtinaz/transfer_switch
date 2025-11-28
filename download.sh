#!/bin/bash

driver_path="/data/apps"
driver_name="transfer_switch"

# check if /data/apps path exists
if [ ! -d "/data/apps" ]; then
    mkdir -p /data/apps
fi

echo ""
echo ""

# fetch version numbers for different versions
echo -n "Fetch current version numbers..."

# latest release
latest_release_stable=$(curl -s https://api.github.com/repos/drtinaz/${driver_name}/releases/latest | grep "tag_name" | cut -d : -f 2,3 | tr -d "\ " | tr -d \" | tr -d \,)

# nightly build
latest_release_nightly=$(curl -s https://api.github.com/repos/drtinaz/${driver_name}/releases | sed -nE 's/.*"tag_name": "([^"]+(rc|master))".*/\1/p' | head -n 1)


echo
PS3=$'\nSelect which version you want to install and enter the corresponding number: '

# create list of versions
version_list=(
    "latest release \"$latest_release_stable\""
    "nightly build \"v$latest_release_nightly\""
    "quit"
)

select version in "${version_list[@]}"
do
    case $version in
        "latest release \"$latest_release_stable\"")
            break
            ;;
        "nightly build \"v$latest_release_nightly\"")
            break
            ;;
        "quit")
            exit 0
            ;;
        *)
            echo "> Invalid option: $REPLY. Please enter a number!"
            ;;
    esac
done

echo "> Selected: $version"
echo ""
echo ""
if [ -d ${driver_path}/${driver_name} ]; then
    echo "Updating driver '$driver_name'..."
else
    echo "Installing driver '$driver_name'..."
fi


# change to temp folder
cd /tmp


# download driver
echo ""
echo "Downloading driver..."


## latest release
if [ "$version" = "latest release \"$latest_release_stable\"" ]; then
    # download latest release
    url=$(curl -s https://api.github.com/repos/drtinaz/${driver_name}/releases/latest | grep "zipball_url" | sed -n 's/.*"zipball_url": "\([^"]*\)".*/\1/p')
fi

## nightly build
if [ "$version" = "nightly build \"v$latest_release_nightly\"" ]; then
    # download nightly build
    url="https://github.com/drtinaz/${driver_name}/archive/refs/heads/master.zip"
fi

echo "Downloading from: $url"
wget -O /tmp/${driver_name}.zip "$url"

# check if download was successful
if [ ! -f /tmp/${driver_name}.zip ]; then
    echo ""
    echo "Download failed. Exiting..."
    exit 1
fi


# If updating: cleanup old folder
if [ -d /tmp/${driver_name}-master ]; then
    rm -rf /tmp/${driver_name}-master
fi


# unzip folder
echo "Unzipping driver..."
unzip ${driver_name}.zip

# Find and rename the extracted folder to be always the same
extracted_folder=$(find /tmp/ -maxdepth 1 -type d -name "*${driver_name}-*")

# Desired folder name
desired_folder="/tmp/${driver_name}-master"

# Check if the extracted folder exists and does not already have the desired name
if [ -n "$extracted_folder" ]; then
    if [ "$extracted_folder" != "$desired_folder" ]; then
        mv "$extracted_folder" "$desired_folder"
    else
        echo "Folder already has the desired name: $desired_folder"
    fi
else
    echo "Error: Could not find extracted folder. Exiting..."
    exit 1
fi


# If updating: cleanup existing driver
if [ -d ${driver_path}/${driver_name} ]; then
    echo ""
    echo "Cleaning up existing driver..."
    rm -rf ${driver_path:?}/${driver_name}
fi


# copy files
echo ""
echo "Copying new driver files..."

cp -R /tmp/${driver_name}-master/ ${driver_path}/${driver_name}/

# remove temp files
echo ""
echo "Cleaning up temp files..."
rm -rf /tmp/${driver_name}.zip
rm -rf /tmp/${driver_name}-master


# set permissions for files
echo ""
echo "Setting permissions for files..."
chmod 755 ${driver_path}/${driver_name}/${driver_name}.py
chmod 755 ${driver_path}/${driver_name}/install.sh
chmod 755 ${driver_path}/${driver_name}/restart.sh
chmod 755 ${driver_path}/${driver_name}/uninstall.sh
chmod 755 ${driver_path}/${driver_name}/service/run
chmod 755 ${driver_path}/${driver_name}/service/log/run

echo ""
echo ""
echo "If this is a first time install, you can execute"
echo "the install.sh script with the following command:"
echo "bash ${driver_path}/${driver_name}/install.sh"
echo ""
echo "or execute the restart.sh script if this is an update to an existing version:"
echo "bash ${driver_path}/${driver_name}/restart.sh"
echo ""
echo
echo "Done."
echo
echo
