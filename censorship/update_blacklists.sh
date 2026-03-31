#!/bin/sh -e

. $(dirname "${0}")/censorship_params.sh

${ROOT_DIR}/update_cncpo.sh || true
${ROOT_DIR}/update_aams.sh  || true
${ROOT_DIR}/update_admt.sh  || true
${ROOT_DIR}/update_manual.sh || true
${ROOT_DIR}/update_agcom.sh  || true
${ROOT_DIR}/update_consob.sh || true
${ROOT_DIR}/update_ivass.sh || true
${ROOT_DIR}/update_cert_agid.sh || true
#${ROOT_DIR}/update_BL_abuse.sh  || true
#${ROOT_DIR}/update_BL_ads.sh  || true
#${ROOT_DIR}/update_BL_crypto.sh  || true
#${ROOT_DIR}/update_BL_drugs.sh  || true
${ROOT_DIR}/update_BL_fakenews.sh  || true
#${ROOT_DIR}/update_BL_fraud.sh  || true
${ROOT_DIR}/update_BL_gambling.sh  || true
${ROOT_DIR}/update_BL_malware.sh  || true
#${ROOT_DIR}/update_BL_phishing.sh  || true
#${ROOT_DIR}/update_BL_piracy.sh  || true
${ROOT_DIR}/update_BL_piracyshield.sh  || true
${ROOT_DIR}/update_BL_porn.sh  || true
#${ROOT_DIR}/update_BL_ransomware.sh  || true
#${ROOT_DIR}/update_BL_redirect.sh  || true
#${ROOT_DIR}/update_BL_scam.sh  || true
#${ROOT_DIR}/update_BL_smarttv.sh  || true
#${ROOT_DIR}/update_BL_tiktok.sh  || true
#${ROOT_DIR}/update_BL_torrent.sh  || true
#${ROOT_DIR}/update_BL_tracking.sh  || true
${ROOT_DIR}/update_BL_urlhaus.sh  || true
#${ROOT_DIR}/update_BL_vaping.sh  || true
#${ROOT_DIR}/BL_concat.sh  || true
/usr/local/etc/rc.d/unbound reload
