import asyncio
import base64

import aiohttp

from .. import fhem, generic


class github_backup(generic.FhemModule):

    headers = {
        "Authorization": "",
        "Accept": "application/vnd.github+json",
    }

    def __init__(self, logger):
        super().__init__(logger)

        self.gh_token_ready = asyncio.Event()

        attr_config = {
            "backup_interval": {
                "default": 24,
                "format": "int",
                "help": "Change interval in hours, default is 24.",
            },
            "github_token": {"default": "", "help": "Personal github token"},
            "backup_files": {
                "default": "fhem.cfg,configDB.db,configDB.conf",
                "help": "Comma separated list of files to backup",
            },
        }
        self.set_attr_config(attr_config)

        set_config = {
            "backup_now": {},
        }
        self.set_set_config(set_config)

    # FHEM FUNCTION
    async def Define(self, hash, args, argsh):
        await super().Define(hash, args, argsh)
        if len(args) != 5:
            return (
                "Usage: define my_backup fhempy github_backup"
                + " https://github.com/xxx/fhem_backup master_fhem_rpi"
            )

        self.url = args[3]
        self.gh_user = self.url.split("/")[-2]
        self.gh_repo = self.url.split("/")[-1]
        self.directory = args[4]

        # do this to check if gh token is set
        await self.set_attr_github_token(hash)

        self.create_async_task(self.update_static_readings())
        self.create_async_task(self.backup_loop())

    async def set_backup_now(self, hash, params):
        self.create_async_task(self.do_backup())

    async def set_attr_github_token(self, hash):
        if self._attr_github_token == "":
            await fhem.readingsSingleUpdate(
                hash, "state", "Please set github_token attribute", 1
            )
            return

        self.gh_token_ready.set()
        github_backup.headers["Authorization"] = f"token {self._attr_github_token}"

    async def github_get(self, url):
        ret = None
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(url, headers=github_backup.headers) as resp:
                if resp.status < 400:
                    ret = await resp.json()
                else:
                    self.logger.error(
                        f"Failed to get {url} with HTTP error {resp.status}"
                    )
        return ret

    async def github_put(self, url, json_data):
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.put(
                url, json=json_data, headers=github_backup.headers
            ) as resp:
                if resp.status < 400:
                    return True
                else:
                    self.logger.error(
                        f"Failed to put {url} with HTTP error {resp.status}"
                    )
        return False

    async def b64encode_file(self, filename):
        fh = open(filename)
        f_content = fh.read()
        fh.close()
        b64_bytes = base64.b64encode(f_content.encode("ascii"))
        return b64_bytes.decode("ascii")

    async def get_sha_from_file(self, filename):
        resp = await self.github_get(
            f"https://api.github.com/repos/{self.gh_user}/"
            + f"{self.gh_repo}/contents/{self.directory}/{filename}"
        )
        if resp is not None and "sha" in resp:
            return resp["sha"]
        return None

    async def upload_file(self, filename):
        try:
            # get current sha
            f_sha = await self.get_sha_from_file(filename)

            content = await self.b64encode_file(filename)

            # upload file
            data_msg = {"message": "fhempy backup", "content": content}
            if f_sha:
                data_msg["sha"] = f_sha

            await self.github_put(
                f"https://api.github.com/repos/{self.gh_user}/"
                + f"{self.gh_repo}/contents/{self.directory}/{filename}",
                data_msg,
            )
            return True
        except Exception:
            await fhem.readingsSingleUpdateIfChanged(
                self.hash, f"{filename}_backup", "failed", 1
            )
            self.logger.exception(f"Failed to upload file {filename}")
        return False

    async def backup_loop(self):
        await self.gh_token_ready.wait()
        while True:
            await self.do_backup(self)
            await asyncio.sleep(self._attr_backup_interval * 3600)

    async def do_backup(self):
        if self._attr_github_token == "":
            return

        for file in self._attr_backup_files.split(","):
            if await self.upload_file(file):
                await self.update_readings(file)

    async def update_static_readings(self):
        await fhem.readingsSingleUpdateIfChanged(
            self.hash,
            "repository",
            f"<html><a href='{self.url}' target='_blank'>"
            + "Open repository (new tab/window)</a></html>",
            1,
        )

    async def update_readings(self, file):
        await fhem.readingsBeginUpdate(self.hash)
        await fhem.readingsBulkUpdateIfChanged(self.hash, f"{file}_backup", "ok")
        await fhem.readingsEndUpdate(self.hash, 1)