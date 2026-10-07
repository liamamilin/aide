// Stable Keychain identity. No UI, networking, arbitrary item names or logging.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <CommonCrypto/CommonDigest.h>
#include <sys/stat.h>
#include <poll.h>
#include <unistd.h>

static BOOL pipeFD(int fd) {
    struct stat info;
    return fstat(fd, &info) == 0 && S_ISFIFO(info.st_mode);
}

static BOOL trustedParent(pid_t parent) {
    if (parent <= 1 || parent != getppid()) return NO;
    SecCodeRef selfCode = NULL, parentCode = NULL;
    CFDictionaryRef info = NULL;
    SecRequirementRef requirement = NULL;
    BOOL valid = NO;
    if (SecCodeCopySelf(kSecCSDefaultFlags, &selfCode) != errSecSuccess) goto finish;
    if (SecCodeCopySigningInformation(selfCode, kSecCSSigningInformation, &info) != errSecSuccess) goto finish;
    {
        NSArray *certificates = ((__bridge NSDictionary *)info)[(__bridge NSString *)kSecCodeInfoCertificates];
        if (!certificates.count) goto finish;
        NSData *cert = CFBridgingRelease(SecCertificateCopyData((__bridge SecCertificateRef)certificates[0]));
        if (!cert.length) goto finish;
        unsigned char digest[CC_SHA1_DIGEST_LENGTH];
        CC_SHA1(cert.bytes, (CC_LONG)cert.length, digest);
        NSMutableString *hash = [NSMutableString string];
        for (int i = 0; i < CC_SHA1_DIGEST_LENGTH; i++) [hash appendFormat:@"%02x", digest[i]];
        NSString *text = [NSString stringWithFormat:@"identifier \"com.milin.ai-desktop-assistant\" and certificate leaf = H\"%@\"", hash];
        if (SecRequirementCreateWithString((__bridge CFStringRef)text, kSecCSDefaultFlags, &requirement) != errSecSuccess) goto finish;
        NSDictionary *attributes = @{(__bridge NSString *)kSecGuestAttributePid: @(parent)};
        if (SecCodeCopyGuestWithAttributes(NULL, (__bridge CFDictionaryRef)attributes, kSecCSDefaultFlags, &parentCode) != errSecSuccess) goto finish;
        valid = SecCodeCheckValidity(parentCode, kSecCSStrictValidate, requirement) == errSecSuccess;
    }
finish:
    if (requirement) CFRelease(requirement);
    if (info) CFRelease(info);
    if (parentCode) CFRelease(parentCode);
    if (selfCode) CFRelease(selfCode);
    return valid;
}

static NSData *readRequest(void) {
    NSMutableData *data = [NSMutableData data];
    // A trusted client still has to finish its request within five seconds.
    for (int ticks = 0; ticks < 50; ticks++) {
        struct pollfd input = {STDIN_FILENO, POLLIN | POLLHUP, 0};
        if (poll(&input, 1, 100) <= 0) continue;
        char buffer[1024];
        ssize_t count = read(STDIN_FILENO, buffer, sizeof buffer);
        if (count == 0) return data;
        if (count < 0 || data.length + count > 8192) return nil;
        [data appendBytes:buffer length:(NSUInteger)count];
    }
    return nil;
}

static BOOL validKey(NSString *key, BOOL allowEmpty) {
    return [key isKindOfClass:NSString.class] && key.length <= 4096 &&
        (allowEmpty || key.length > 0) &&
        [key rangeOfCharacterFromSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].location == NSNotFound;
}

int main(void) {
    @autoreleasepool {
        if (!pipeFD(STDIN_FILENO) || !pipeFD(STDOUT_FILENO) || !trustedParent(getppid())) return 2;
        NSData *data = readRequest();
        if (!data) return 2;
        id request = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if (![request isKindOfClass:NSDictionary.class] || ![request[@"parent"] isKindOfClass:NSNumber.class] ||
            [request[@"parent"] intValue] != getppid()) return 2;
        NSString *provider = request[@"provider"], *operation = request[@"operation"];
        if (![@[@"parallel", @"exa"] containsObject:provider] ||
            ![@[@"get", @"set", @"delete", @"probe"] containsObject:operation]) return 2;
        // Test items use a separate namespace; production records are never touched by smoke tests.
        NSString *service = @"com.milin.ai-desktop-assistant.web-search";
        NSString *account = provider;
        id fixture = request[@"fixture"];
        if (fixture) {
            NSCharacterSet *hex = [NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"];
            if (![fixture isKindOfClass:NSString.class] || [fixture length] != 32 ||
                [fixture rangeOfCharacterFromSet:hex.invertedSet].location != NSNotFound) return 2;
            service = @"com.milin.ai-desktop-assistant.web-search.fixture";
            account = [NSString stringWithFormat:@"%@.%@", fixture, provider];
        }
        BOOL interactive = [request[@"interactive"] isEqual:@YES];
        if (SecKeychainSetUserInteractionAllowed(interactive) != errSecSuccess) return 1;
        NSMutableDictionary *query = [@{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
                                       (__bridge id)kSecAttrService: service,
                                       (__bridge id)kSecAttrAccount: account} mutableCopy];
        if (!interactive) query[(__bridge id)kSecUseAuthenticationUI] = (__bridge id)kSecUseAuthenticationUIFail;
        NSString *key = @"";
        OSStatus status = errSecSuccess;
        if ([operation isEqual:@"get"]) {
            query[(__bridge id)kSecReturnData] = @YES;
            query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
            CFTypeRef value = NULL;
            status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &value);
            if (status == errSecSuccess) {
                if (!value || CFGetTypeID(value) != CFDataGetTypeID()) { if (value) CFRelease(value); return 1; }
                key = [[NSString alloc] initWithData:(__bridge NSData *)value encoding:NSUTF8StringEncoding];
                CFRelease(value);
                if (!validKey(key, YES)) return 1;
            } else if (status == errSecItemNotFound) status = errSecSuccess;
        } else if ([operation isEqual:@"set"]) {
            NSString *value = request[@"key"];
            if (!validKey(value, NO)) return 2;
            NSDictionary *attributes = @{(__bridge id)kSecValueData: [value dataUsingEncoding:NSUTF8StringEncoding]};
            status = SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)attributes);
            if (status == errSecItemNotFound) {
                [query addEntriesFromDictionary:attributes];
                status = SecItemAdd((__bridge CFDictionaryRef)query, NULL);
            }
        } else if ([operation isEqual:@"delete"]) {
            status = SecItemDelete((__bridge CFDictionaryRef)query);
            if (status == errSecItemNotFound) status = errSecSuccess;
        }
        if (status != errSecSuccess) return 1;
        NSData *reply = [NSJSONSerialization dataWithJSONObject:@{@"key": key} options:0 error:NULL];
        if (!reply || reply.length > 8192) return 1;
        const char *bytes = reply.bytes;
        size_t remaining = reply.length;
        while (remaining) {
            ssize_t count = write(STDOUT_FILENO, bytes, remaining);
            if (count <= 0) return 1;
            bytes += count; remaining -= (size_t)count;
        }
        return 0;
    }
}
