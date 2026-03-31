#!/bin/sh -e

. $(dirname "${0}")/censorship_params.sh

LIST_FILE="${TMP_DL_DIR}/blacklist_ivass.txt"
LIST_OUT="${UNBOUND_CONF_DIR}/db.blacklist_ivass.conf"
LIST_TYPE="ivass"
BLACKHOLE="127.0.0.1"

PARSER_OPTS="-i ${LIST_FILE} -o ${LIST_OUT} -f ${OUTPUT_FORMAT} -d ${LIST_TYPE} -b ${BLACKHOLE}"

## downloading ###############################################################
python3 $(dirname "${0}")/download_ivass.py -o ${LIST_FILE}

## parsing ###################################################################
${PARSER_BIN} ${PARSER_OPTS}
