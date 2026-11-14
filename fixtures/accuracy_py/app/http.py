class Session:
    def send(self, req):
        return req

    def get(self, url):
        return self.send(url)
