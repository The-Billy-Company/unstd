Supply chain checks now validate dependency sources and hashes, audit every
locked extra and build/release tool, and retain a complete CycloneDX inventory.
The required CI gate also rescans daily for newly published advisories.

Release wheels build from the published source archive with authenticated build
and test requirements. Before PyPI publication, archive, metadata and wheel
RECORD checks produce a SHA256 manifest that is verified again in the isolated
Trusted Publishing job. Test reports and dependency evidence stay outside the
distribution handoff.
